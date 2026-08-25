"""Declarative board configuration: prefix, statuses, labels, milestones, policy."""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .db import Board, CATEGORIES, PRIORITIES


CONFIG_SCHEMA_VERSION = 2

STATUS_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{0,31}$")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class BoardConfig:
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


def load_config(path: str | Path) -> BoardConfig:
    config_path = Path(path)
    try:
        with config_path.open("rb") as source:
            data = tomllib.load(source)
    except FileNotFoundError as exc:
        raise ConfigError(f"configuration not found: {config_path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {config_path}: {exc}") from exc
    validate_config(data)
    return BoardConfig(config_path, data, _canonical_digest(data))


def validate_config(data: dict[str, Any]) -> None:
    version = data.get("schema_version")
    if version != CONFIG_SCHEMA_VERSION:
        raise ConfigError(
            f"unsupported config schema_version {version!r}; expected {CONFIG_SCHEMA_VERSION}"
        )
    project = data.get("project")
    if not isinstance(project, dict):
        raise ConfigError("[project] is required")
    prefix = project.get("prefix", "")
    if not isinstance(prefix, str) or not prefix.isalnum() or not 2 <= len(prefix) <= 10:
        raise ConfigError("project.prefix must be 2-10 alphanumeric characters")
    if not isinstance(project.get("name"), str) or not project["name"].strip():
        raise ConfigError("project.name is required")

    defaults = data.get("defaults", {})
    if not isinstance(defaults, dict):
        raise ConfigError("[defaults] must be a table")
    if defaults.get("priority", "medium") not in PRIORITIES:
        raise ConfigError("defaults.priority is invalid")

    policy = data.get("agent_policy", {})
    if not isinstance(policy, dict):
        raise ConfigError("[agent_policy] must be a table")
    if "require_assignee_before_start" in policy and not isinstance(
        policy["require_assignee_before_start"], bool
    ):
        raise ConfigError("agent_policy.require_assignee_before_start must be boolean")

    statuses = data.get("statuses", [])
    if not isinstance(statuses, list) or not statuses:
        raise ConfigError("at least one [[statuses]] entry is required")
    names: set[str] = set()
    active = False
    for status in statuses:
        if not isinstance(status, dict) or not status.get("name") or not status.get("category"):
            raise ConfigError("each [[statuses]] entry requires name and category")
        if not STATUS_NAME_PATTERN.fullmatch(status["name"]):
            raise ConfigError(f"invalid status name: {status['name']!r}")
        if status["category"] not in CATEGORIES:
            raise ConfigError(f"invalid status category: {status['category']!r}")
        if status["name"] in names:
            raise ConfigError("status names must be unique")
        names.add(status["name"])
        if status["category"] in ("backlog", "unstarted", "started"):
            active = True
    if not active:
        raise ConfigError("at least one status must be in an active category")

    label_keys: set[str] = set()
    label_names: set[str] = set()
    for label in data.get("labels", []):
        if not isinstance(label, dict) or not label.get("key") or not label.get("name"):
            raise ConfigError("each [[labels]] entry requires key and name")
        if label["key"] in label_keys or label["name"] in label_names:
            raise ConfigError("label keys and names must be unique")
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", label.get("color", "#64748b")):
            raise ConfigError(f"invalid color for label {label['key']}")
        label_keys.add(label["key"])
        label_names.add(label["name"])

    milestone_keys: set[str] = set()
    for milestone in data.get("milestones", []):
        if not isinstance(milestone, dict) or not milestone.get("key") or not milestone.get("name"):
            raise ConfigError("each [[milestones]] entry requires key and name")
        if milestone["key"] in milestone_keys:
            raise ConfigError("milestone keys must be unique")
        milestone_keys.add(milestone["key"])


def default_config(project_name: str, prefix: str) -> str:
    return f'''schema_version = 2

[project]
prefix = "{prefix}"
name = "{project_name}"
description = ""

[defaults]
priority = "medium"

[agent_policy]
require_assignee_before_start = true

# Status names are yours to change; categories are the contract.
# Transitions are free: any status can move to any other status.
[[statuses]]
name = "Backlog"
category = "backlog"

[[statuses]]
name = "Todo"
category = "unstarted"

[[statuses]]
name = "In Progress"
category = "started"

[[statuses]]
name = "In Review"
category = "started"

[[statuses]]
name = "Done"
category = "completed"

[[statuses]]
name = "Canceled"
category = "canceled"

# Starter labels: working documentation of the label-over-issue-type model.
# Rename, recolor, or remove them freely.
[[labels]]
key = "bug"
name = "Bug"
color = "#ef4444"

[[labels]]
key = "feature"
name = "Feature"
color = "#3b82f6"

[[labels]]
key = "chore"
name = "Chore"
color = "#64748b"

[[labels]]
key = "review_required"
name = "Review required"
color = "#f59e0b"

[[labels]]
key = "reviewed"
name = "Reviewed"
color = "#22c55e"
'''


class ConfigService:
    def __init__(self, board: Board):
        self.board = board

    def plan(self, config: BoardConfig) -> dict[str, Any]:
        with self.board.connect() as db:
            plan = self._plan(config, db)
        return plan

    def _plan(self, config: BoardConfig, db: Any) -> dict[str, Any]:
        actions: list[dict[str, Any]] = []
        current = db.execute("SELECT * FROM board WHERE id=1").fetchone()
        desired_board = (
            config.project["prefix"].upper(),
            config.project["name"],
            config.project.get("description", ""),
        )
        if not current:
            actions.append({"action": "create", "entity": "board", "key": desired_board[0]})
        elif (current["prefix"], current["name"], current["description"]) != desired_board:
            actions.append({"action": "update", "entity": "board", "key": desired_board[0]})

        current_statuses = [
            (row["name"], row["category"])
            for row in db.execute("SELECT name,category FROM statuses ORDER BY position,id")
        ]
        desired_statuses = [(status["name"], status["category"]) for status in config.data["statuses"]]
        if current_statuses != desired_statuses:
            actions.append({"action": "replace", "entity": "statuses", "key": "*"})

        current_labels = {
            row["key"]: row for row in db.execute("SELECT * FROM labels WHERE key IS NOT NULL")
        }
        for label in config.data.get("labels", []):
            row = current_labels.get(label["key"])
            if not row:
                actions.append({"action": "create", "entity": "label", "key": label["key"]})
            elif (row["name"], row["color"], row["managed_by"]) != (
                label["name"], label.get("color", "#64748b"), "config"
            ):
                actions.append({"action": "update", "entity": "label", "key": label["key"]})

        current_milestones = {
            row["key"]: row for row in db.execute("SELECT * FROM milestones WHERE key IS NOT NULL")
        }
        for milestone in config.data.get("milestones", []):
            row = current_milestones.get(milestone["key"])
            desired = (milestone["name"], milestone.get("description", ""), milestone.get("due_at"), "config")
            if not row:
                actions.append({"action": "create", "entity": "milestone", "key": milestone["key"]})
            elif (row["name"], row["description"], row["due_at"], row["managed_by"]) != desired:
                actions.append({"action": "update", "entity": "milestone", "key": milestone["key"]})

        previous_digest = current["config_digest"] if current else None
        return {
            "config": str(config.path),
            "digest": config.digest,
            "previous_digest": previous_digest,
            "actions": actions,
            "changed": bool(actions) or previous_digest != config.digest,
        }

    def apply(self, config: BoardConfig, actor_id: int | None = None) -> dict[str, Any]:
        """Reconcile in one write transaction; the plan is computed on the same snapshot."""
        if actor_id is not None:
            self.board.require_role(actor_id, "admin")
        with self.board.transaction() as db:
            plan = self._plan(config, db)
            if not plan["changed"]:
                return {**plan, "applied": False}
            self.board.configure_board(
                config.project["prefix"],
                config.project["name"],
                config.project.get("description", ""),
                defaults=config.data.get("defaults", {}),
                agent_policy=config.data.get("agent_policy", {}),
                config_digest=config.digest,
                db=db,
            )
            self.board.replace_statuses(config.data["statuses"], db=db)
            self.board.replace_labels(config.data.get("labels", []), db=db)
            self.board.replace_milestones(config.data.get("milestones", []), db=db)
            self.board._activity(
                db, actor_id, "board", 1, "config_applied",
                {"digest": config.digest, "actions": plan["actions"]},
            )
        return {**plan, "applied": True}


def suggested_prefix(name: str) -> str:
    prefix = re.sub(r"[^A-Za-z0-9]", "", name).upper()[:10]
    return prefix if len(prefix) >= 2 else "APP"
