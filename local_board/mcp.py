from __future__ import annotations

import json
from typing import Any

from .db import Board, ConflictError, ISSUE_TYPES, PRIORITIES


ISSUE_REF = {"description": "Stable issue identifier such as APP-12, or a local numeric ID", "oneOf": [{"type": "string", "pattern": "^[A-Za-z0-9]{2,10}-[1-9][0-9]*$"}, {"type": "integer", "minimum": 1}]}
PROJECT_REF = {"description": "Project key such as APP, or a local numeric ID", "oneOf": [{"type": "string"}, {"type": "integer", "minimum": 1}]}


def obj(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}


def tool(name: str, description: str, properties: dict[str, Any] | None = None, required: list[str] | None = None) -> dict[str, Any]:
    return {"name": name, "description": description, "inputSchema": obj(properties or {}, required)}


TOOLS = [
    tool("whoami", "Return the authenticated actor identity."),
    tool("list_actors", "List human and agent identities available for assignment."),
    tool("list_projects", "List projects."),
    tool("get_project_context", "Get project metadata, workflows, labels, milestones, defaults, and agent policy.", {"project": PROJECT_REF}, ["project"]),
    tool("list_workflows", "List workflows configured for a project.", {"project": PROJECT_REF}, ["project"]),
    tool("list_labels", "List labels configured for a project.", {"project": PROJECT_REF}, ["project"]),
    tool("list_milestones", "List milestones configured for a project.", {"project": PROJECT_REF}, ["project"]),
    tool("create_project", "Create a manually managed project.", {"key": {"type": "string", "pattern": "^[A-Za-z0-9]{2,10}$"}, "name": {"type": "string", "minLength": 1}, "description": {"type": "string", "default": ""}}, ["key", "name"]),
    tool("list_issues", "Search issues and return summaries including stable identifiers and revisions.", {"project": PROJECT_REF, "status": {"type": "string"}, "query": {"type": "string"}}),
    tool("get_issue_context", "Get the complete issue context required for agent work.", {"issue": ISSUE_REF}, ["issue"]),
    tool("get_available_transitions", "Get policy-aware transitions for the current issue revision.", {"issue": ISSUE_REF}, ["issue"]),
    tool("create_issue", "Create an issue using stable actor and project references.", {"project": PROJECT_REF, "title": {"type": "string", "minLength": 1}, "type": {"type": "string", "enum": list(ISSUE_TYPES)}, "description": {"type": "string", "default": ""}, "priority": {"type": "string", "enum": list(PRIORITIES)}, "milestone": {"oneOf": [{"type": "string"}, {"type": "integer"}]}, "assignee": {"oneOf": [{"type": "string"}, {"type": "integer"}]}, "reviewer": {"oneOf": [{"type": "string"}, {"type": "integer"}]}}, ["project", "title"]),
    tool("update_issue", "Update issue fields if expected_revision is current.", {"issue": ISSUE_REF, "expected_revision": {"type": "integer", "minimum": 1}, "title": {"type": "string", "minLength": 1}, "description": {"type": "string"}, "priority": {"type": "string", "enum": list(PRIORITIES)}, "milestone": {"oneOf": [{"type": "string"}, {"type": "integer"}, {"type": "null"}]}, "assignee": {"oneOf": [{"type": "string"}, {"type": "integer"}, {"type": "null"}]}, "reviewer": {"oneOf": [{"type": "string"}, {"type": "integer"}, {"type": "null"}]}, "position": {"type": "number"}}, ["issue", "expected_revision"]),
    tool("transition_issue", "Move an issue through an allowed workflow transition.", {"issue": ISSUE_REF, "status": {"type": "string"}, "expected_revision": {"type": "integer", "minimum": 1}}, ["issue", "status", "expected_revision"]),
    tool("claim_issue", "Atomically claim an issue for the authenticated actor.", {"issue": ISSUE_REF, "expected_revision": {"type": "integer", "minimum": 1}, "lease_seconds": {"type": "integer", "minimum": 60, "maximum": 86400, "default": 1800}}, ["issue", "expected_revision"]),
    tool("release_issue", "Release an issue claimed by the authenticated actor.", {"issue": ISSUE_REF, "expected_revision": {"type": "integer", "minimum": 1}}, ["issue", "expected_revision"]),
    tool("create_milestone", "Create a project milestone.", {"project": PROJECT_REF, "key": {"type": "string"}, "name": {"type": "string", "minLength": 1}, "description": {"type": "string"}, "due_at": {"type": "string"}}, ["project", "name"]),
    tool("set_workflow", "Configure a manually managed workflow.", {"project": PROJECT_REF, "issue_type": {"type": "string", "enum": list(ISSUE_TYPES)}, "states": {"type": "array", "items": {"type": "string"}, "minItems": 1}, "transitions": {"type": "array", "items": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 2}},}, ["project", "issue_type", "states", "transitions"]),
    tool("add_comment", "Add a Markdown comment.", {"issue": ISSUE_REF, "body": {"type": "string", "minLength": 1}}, ["issue", "body"]),
    tool("update_comment", "Edit a comment.", {"comment_id": {"type": "integer"}, "body": {"type": "string", "minLength": 1}}, ["comment_id", "body"]),
    tool("delete_comment", "Delete a comment.", {"comment_id": {"type": "integer"}}, ["comment_id"]),
    tool("add_checklist_item", "Add a checklist item.", {"issue": ISSUE_REF, "text": {"type": "string", "minLength": 1}, "completed": {"type": "boolean", "default": False}, "position": {"type": "number", "default": 0}}, ["issue", "text"]),
    tool("update_checklist_item", "Edit or complete a checklist item.", {"item_id": {"type": "integer"}, "text": {"type": "string"}, "completed": {"type": "boolean"}}, ["item_id"]),
    tool("delete_checklist_item", "Delete a checklist item.", {"item_id": {"type": "integer"}}, ["item_id"]),
    tool("add_dependency", "Add a blocking or related issue dependency.", {"issue": ISSUE_REF, "depends_on": ISSUE_REF, "relation": {"type": "string", "enum": ["blocks", "related"], "default": "blocks"}}, ["issue", "depends_on"]),
    tool("remove_dependency", "Remove an issue dependency.", {"issue": ISSUE_REF, "depends_on": ISSUE_REF, "relation": {"type": "string", "enum": ["blocks", "related"], "default": "blocks"}}, ["issue", "depends_on"]),
    tool("add_attachment", "Attach a repository-local file reference.", {"issue": ISSUE_REF, "name": {"type": "string"}, "path": {"type": "string"}, "media_type": {"type": "string"}}, ["issue", "name", "path"]),
    tool("delete_attachment", "Delete an attachment reference.", {"attachment_id": {"type": "integer"}}, ["attachment_id"]),
    tool("create_label", "Create a label in a manually managed project.", {"project": PROJECT_REF, "key": {"type": "string"}, "name": {"type": "string"}, "color": {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$", "default": "#64748b"}}, ["project", "name"]),
    tool("add_label", "Attach a project label by key, name, or numeric ID.", {"issue": ISSUE_REF, "label": {"oneOf": [{"type": "string"}, {"type": "integer"}]}}, ["issue", "label"]),
    tool("remove_label", "Remove a label from an issue.", {"issue": ISSUE_REF, "label": {"oneOf": [{"type": "string"}, {"type": "integer"}]}}, ["issue", "label"]),
    tool("add_git_link", "Associate a branch, commit, PR, or MR.", {"issue": ISSUE_REF, "link_kind": {"type": "string", "enum": ["branch", "commit", "pr", "mr"], "default": "branch"}, "ref": {"type": "string"}, "url": {"type": "string"}}, ["issue", "ref"]),
    tool("delete_git_link", "Delete a Git link.", {"link_id": {"type": "integer"}}, ["link_id"]),
    tool("list_activity", "Read activity entries.", {"entity_type": {"type": "string"}, "entity_id": {"type": "integer"}, "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100}}),
    tool("update_activity", "Correct an editable activity entry.", {"activity_id": {"type": "integer"}, "action": {"type": "string"}, "data": {"type": "object"}}, ["activity_id"]),
    tool("delete_activity", "Delete an editable activity entry.", {"activity_id": {"type": "integer"}}, ["activity_id"]),
]


def schemas() -> list[dict[str, Any]]:
    return TOOLS


def _actor_id(board: Board, value: Any) -> int | None:
    return None if value is None else board.get_actor(value)["id"]


def _label_id(board: Board, issue_id: int, value: int | str) -> int:
    if isinstance(value, int): return value
    issue = board.get_issue(issue_id)
    context = board.project_context(issue["project_id"])
    found = next((label for label in context["labels"] if label.get("key") == value or label["name"] == value), None)
    if not found: raise KeyError("label not found")
    return found["id"]


def call_tool(board: Board, actor: int, name: str, args: dict[str, Any]) -> Any:
    args = dict(args)
    if name == "whoami": return board.get_actor(actor)
    if name == "list_actors": return board.list_actors()
    if name == "list_projects": return board.list_projects()
    if name == "get_project_context": return board.project_context(args["project"])
    if name in {"list_workflows", "list_labels", "list_milestones"}: return board.project_context(args["project"])[name.removeprefix("list_")]
    if name == "create_project": return board.create_project(actor, **args)
    if name == "list_issues":
        if "project" in args: args["project_id"] = board.resolve_project(args.pop("project"))
        return board.list_issues(**args)
    if name == "get_issue_context": return board.get_issue_context(args["issue"])
    if name == "get_available_transitions":
        context = board.get_issue_context(args["issue"])
        return {"issue": context["identifier"], "revision": context["revision"], "status": context["status"], "blocked": context["blocked"], "transitions": context["available_transitions"]}
    if name == "create_issue":
        args["project_id"] = board.resolve_project(args.pop("project"))
        if "type" in args: args["issue_type"] = args.pop("type")
        if "assignee" in args: args["assignee_id"] = _actor_id(board, args.pop("assignee"))
        if "reviewer" in args: args["reviewer_id"] = _actor_id(board, args.pop("reviewer"))
        if "milestone" in args: args["milestone_id"] = args.pop("milestone") if isinstance(args["milestone"], int) else _milestone_id(board, args["project_id"], args.pop("milestone"))
        return board.create_issue(actor, **args)
    if name == "update_issue":
        issue_id = board.resolve_issue(args.pop("issue"))
        if "assignee" in args: args["assignee_id"] = _actor_id(board, args.pop("assignee"))
        if "reviewer" in args: args["reviewer_id"] = _actor_id(board, args.pop("reviewer"))
        if "milestone" in args:
            value = args.pop("milestone"); project_id = board.get_issue(issue_id)["project_id"]
            args["milestone_id"] = None if value is None else value if isinstance(value, int) else _milestone_id(board, project_id, value)
        return board.update_issue(actor, issue_id, **args)
    if name in {"transition_issue", "claim_issue", "release_issue"}:
        args["issue_id"] = board.resolve_issue(args.pop("issue"))
        return getattr(board, name)(actor, **args)
    if name == "create_milestone": args["project_id"] = board.resolve_project(args.pop("project")); return board.create_milestone(actor, **args)
    if name == "set_workflow": args["project_id"] = board.resolve_project(args.pop("project")); return board.set_workflow(actor, **args)
    if name in {"add_comment", "add_checklist_item", "add_attachment", "add_git_link"}:
        issue_id = board.resolve_issue(args.pop("issue")); kind = name.removeprefix("add_").replace("checklist_item", "checklist")
        return board.add_related(actor, issue_id, kind, **args)
    if name == "add_dependency":
        issue_id = board.resolve_issue(args.pop("issue")); args["depends_on_id"] = board.resolve_issue(args.pop("depends_on")); return board.add_related(actor, issue_id, "dependency", **args)
    if name in {"update_comment", "delete_comment", "update_checklist_item", "delete_checklist_item", "delete_attachment", "delete_git_link"}: return getattr(board, name)(actor, **args)
    if name == "remove_dependency": return board.remove_dependency(actor, board.resolve_issue(args["issue"]), board.resolve_issue(args["depends_on"]), args.get("relation", "blocks"))
    if name == "create_label": args["project_id"] = board.resolve_project(args.pop("project")); return board.create_label(actor, **args)
    if name in {"add_label", "remove_label"}:
        issue_id = board.resolve_issue(args["issue"]); label_id = _label_id(board, issue_id, args["label"]); return getattr(board, name)(actor, issue_id, label_id)
    if name == "list_activity": return board.activity(**args)
    if name == "update_activity": return board.update_activity(**args)
    if name == "delete_activity": return board.delete_activity(**args)
    raise KeyError(f"unknown tool: {name}")


def _milestone_id(board: Board, project_id: int, value: str) -> int:
    milestone = next((item for item in board.project_context(project_id)["milestones"] if item.get("key") == value or item["name"] == value), None)
    if not milestone: raise KeyError("milestone not found")
    return milestone["id"]


def handle(board: Board, actor: int, request: dict[str, Any]) -> dict[str, Any] | None:
    request_id = request.get("id")
    method = request.get("method")
    if method == "notifications/initialized": return None
    if method == "initialize":
        result = {"protocolVersion": "2025-03-26", "capabilities": {"tools": {"listChanged": False}}, "serverInfo": {"name": "local-board", "version": "0.1.0"}}
    elif method == "ping": result = {}
    elif method == "tools/list": result = {"tools": schemas()}
    elif method == "tools/call":
        params = request.get("params", {})
        try:
            value = call_tool(board, actor, params["name"], params.get("arguments", {}))
            result = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, default=str)}], "structuredContent": value}
        except (ConflictError, ValueError, KeyError, TypeError) as exc:
            message = str(exc).strip("'")
            code = "conflict" if isinstance(exc, ConflictError) else "not_found" if isinstance(exc, KeyError) else "blocked" if "blocked" in message or "claimed or assigned" in message else "invalid_request"
            error = {"code": code, "message": message, "retryable": isinstance(exc, ConflictError)}
            result = {"content": [{"type": "text", "text": json.dumps(error)}], "structuredContent": {"error": error}, "isError": True}
    else:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}}
    return {"jsonrpc": "2.0", "id": request_id, "result": result}
