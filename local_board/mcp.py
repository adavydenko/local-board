from __future__ import annotations

import json
from typing import Any

from .db import Board


TOOLS = [
    ("list_projects", "List projects", {}),
    ("create_project", "Create a project", {"key": "string", "name": "string", "description": "string"}),
    ("list_issues", "Search and list issues", {"project_id": "integer", "status": "string", "query": "string"}),
    ("get_issue", "Get an issue with checklist and labels", {"issue_id": "integer"}),
    ("create_issue", "Create task, bug, feature, chore, or epic", {"project_id": "integer", "title": "string", "type": "string", "description": "string", "priority": "string", "milestone_id": "integer", "assignee_id": "integer", "reviewer_id": "integer"}),
    ("update_issue", "Update issue fields", {"issue_id": "integer", "title": "string", "description": "string", "priority": "string", "milestone_id": "integer", "assignee_id": "integer", "reviewer_id": "integer", "position": "number"}),
    ("transition_issue", "Move issue along its configured workflow", {"issue_id": "integer", "status": "string"}),
    ("create_milestone", "Create a project milestone", {"project_id": "integer", "name": "string", "description": "string", "due_at": "string"}),
    ("set_workflow", "Configure states and allowed transitions for an issue type", {"project_id": "integer", "issue_type": "string", "states": "array", "transitions": "array"}),
    ("add_comment", "Add a Markdown comment", {"issue_id": "integer", "body": "string"}),
    ("add_checklist_item", "Add a checklist item", {"issue_id": "integer", "text": "string", "completed": "boolean", "position": "number"}),
    ("add_dependency", "Connect blocking or related issues", {"issue_id": "integer", "depends_on_id": "integer", "relation": "string"}),
    ("add_attachment", "Attach a repository-local file reference", {"issue_id": "integer", "name": "string", "path": "string", "media_type": "string"}),
    ("create_label", "Create a project label", {"project_id": "integer", "name": "string", "color": "string"}),
    ("add_label", "Attach a label to an issue", {"issue_id": "integer", "label_id": "integer"}),
    ("add_git_link", "Associate a branch, commit, PR, or MR", {"issue_id": "integer", "link_kind": "string", "ref": "string", "url": "string"}),
    ("list_activity", "Read the editable activity journal", {"entity_type": "string", "entity_id": "integer", "limit": "integer"}),
    ("update_activity", "Correct an activity journal entry", {"activity_id": "integer", "action": "string", "data": "object"}),
    ("delete_activity", "Delete an activity journal entry", {"activity_id": "integer"}),
]


def schemas() -> list[dict[str, Any]]:
    result = []
    required_by_tool = {"create_project": ["key", "name"], "get_issue": ["issue_id"], "create_issue": ["project_id", "title"], "update_issue": ["issue_id"], "transition_issue": ["issue_id", "status"], "create_milestone": ["project_id", "name"], "set_workflow": ["project_id", "issue_type", "states", "transitions"], "add_comment": ["issue_id", "body"], "add_checklist_item": ["issue_id", "text"], "add_dependency": ["issue_id", "depends_on_id"], "add_attachment": ["issue_id", "name", "path"], "create_label": ["project_id", "name"], "add_label": ["issue_id", "label_id"], "add_git_link": ["issue_id", "ref"], "update_activity": ["activity_id"], "delete_activity": ["activity_id"]}
    for name, description, props in TOOLS:
        result.append({"name": name, "description": description, "inputSchema": {"type": "object", "properties": {k: {"type": v} for k, v in props.items()}, "required": required_by_tool.get(name, []), "additionalProperties": False}})
    return result


def call_tool(board: Board, actor: int, name: str, args: dict[str, Any]) -> Any:
    if name == "list_projects": return board.list_projects()
    if name == "create_project": return board.create_project(actor, **args)
    if name == "list_issues": return board.list_issues(**args)
    if name == "get_issue": return board.get_issue(**args)
    if name == "create_issue":
        if "type" in args: args["issue_type"] = args.pop("type")
        return board.create_issue(actor, **args)
    if name == "update_issue":
        issue_id = args.pop("issue_id"); return board.update_issue(actor, issue_id, **args)
    if name == "transition_issue": return board.transition_issue(actor, **args)
    if name == "create_milestone": return board.create_milestone(actor, **args)
    if name == "set_workflow": return board.set_workflow(actor, **args)
    if name.startswith("add_") and name.removeprefix("add_") in {"comment", "checklist_item", "dependency", "attachment", "git_link"}:
        issue_id = args.pop("issue_id"); kind = name.removeprefix("add_").replace("checklist_item", "checklist")
        return board.add_related(actor, issue_id, kind, **args)
    if name == "create_label": return board.create_label(actor, **args)
    if name == "add_label": return board.add_label(actor, **args)
    if name == "list_activity": return board.activity(**args)
    if name == "update_activity": return board.update_activity(**args)
    if name == "delete_activity": return board.delete_activity(**args)
    raise KeyError(f"unknown tool: {name}")


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
            value = call_tool(board, actor, params["name"], dict(params.get("arguments", {})))
            result = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, default=str)}], "structuredContent": value}
        except (ValueError, KeyError, TypeError) as exc:
            result = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    else:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}}
    return {"jsonrpc": "2.0", "id": request_id, "result": result}
