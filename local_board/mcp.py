from __future__ import annotations

import copy
import json
from typing import Any

from . import __version__
from .db import AuthorizationError, Board, ConflictError, DatabaseBusyError, InvalidTransitionError, ISSUE_TYPES, PRIORITIES


ISSUE_REF = {"description": "Stable issue identifier.", "oneOf": [{"type": "string", "pattern": "^[A-Za-z0-9]{2,10}-[1-9][0-9]*$"}], "examples": ["APP-12"]}
PROJECT_REF = {"type": "string", "description": "Stable project key.", "pattern": "^[A-Za-z0-9]{2,10}$", "examples": ["APP"]}
ACTOR_REF = {"type": "string", "description": "Stable actor name.", "minLength": 1, "examples": ["coding-agent"]}
MILESTONE_REF = {"type": "string", "description": "Stable milestone key.", "minLength": 1, "examples": ["v1"]}
LABEL_REF = {"type": "string", "description": "Stable label key.", "minLength": 1, "examples": ["backend"]}
RELATED_REF = {"type": "string", "description": "Opaque stable key returned for the related object by get_issue_context.", "pattern": "^[A-Za-z0-9]{2,10}-[1-9][0-9]*:(comment|checklist|attachment|git):[1-9][0-9]*$", "examples": ["APP-12:comment:3"]}
ERROR_RESPONSES = {
    "not_found": "The stable identifier or key does not exist.",
    "conflict": "The supplied revision is stale or the requested write conflicts with current state.",
    "invalid_transition": "The target state is not reachable from the current workflow state.",
    "blocked": "Policy or an incomplete dependency prevents the operation.",
    "unauthorized": "The authenticated actor is not allowed to perform the operation.",
    "retryable": "A transient storage or coordination failure may succeed when retried.",
}


def obj(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}


def tool(name: str, description: str, properties: dict[str, Any] | None = None, required: list[str] | None = None) -> dict[str, Any]:
    return {"name": name, "description": description, "inputSchema": obj(properties or {}, required)}


TOOLS = [
    tool("whoami", "Return the authenticated actor identity."),
    tool("list_actors", "List human and agent identities available for assignment."),
    tool("create_actor", "Provision an actor and return its bearer token once. Admin only; transfer the token through a secure out-of-band channel.", {"name": {"type": "string", "minLength": 1}, "kind": {"type": "string", "enum": ["agent", "human"], "default": "agent"}, "role": {"type": "string", "enum": ["member", "viewer"], "default": "member"}}, ["name"]),
    tool("rotate_actor_token", "Invalidate an actor token and return its replacement once. Admin only.", {"actor": ACTOR_REF}, ["actor"]),
    tool("set_actor_role", "Change an actor role. Admin only.", {"actor": ACTOR_REF, "role": {"type": "string", "enum": ["admin", "member", "viewer"]}}, ["actor", "role"]),
    tool("list_projects", "List projects."),
    tool("get_project_context", "Get project metadata, workflows, labels, milestones, defaults, and agent policy.", {"project": PROJECT_REF}, ["project"]),
    tool("list_workflows", "List workflows configured for a project.", {"project": PROJECT_REF}, ["project"]),
    tool("get_workflow", "Get one workflow by project key and issue type.", {"project": PROJECT_REF, "issue_type": {"type": "string", "enum": list(ISSUE_TYPES), "examples": ["feature"]}}, ["project", "issue_type"]),
    tool("list_labels", "List labels configured for a project.", {"project": PROJECT_REF}, ["project"]),
    tool("get_label", "Get one label by project and stable label key.", {"project": PROJECT_REF, "label": LABEL_REF}, ["project", "label"]),
    tool("list_milestones", "List milestones configured for a project.", {"project": PROJECT_REF}, ["project"]),
    tool("get_milestone", "Get one milestone by project and stable milestone key.", {"project": PROJECT_REF, "milestone": MILESTONE_REF}, ["project", "milestone"]),
    tool("list_releases", "List project releases and lifecycle state.", {"project": PROJECT_REF}, ["project"]),
    tool("create_release", "Create a planned project release.", {"project": PROJECT_REF, "name": {"type": "string", "minLength": 1}, "version": {"type": "string", "minLength": 1}, "description": {"type": "string"}, "target_at": {"type": "string"}}, ["project", "name", "version"]),
    tool("transition_release", "Move a release through planned, active, released, or cancelled.", {"release_id": {"type": "integer", "minimum": 1}, "status": {"type": "string", "enum": ["active", "released", "cancelled"]}, "expected_revision": {"type": "integer", "minimum": 1}}, ["release_id", "status", "expected_revision"]),
    tool("create_project", "Create a manually managed project.", {"key": {"type": "string", "pattern": "^[A-Za-z0-9]{2,10}$"}, "name": {"type": "string", "minLength": 1}, "description": {"type": "string", "default": ""}}, ["key", "name"]),
    tool("list_issues", "Search issues and return summaries including stable identifiers and revisions.", {"project": PROJECT_REF, "status": {"type": "string"}, "query": {"type": "string"}}),
    tool("get_issue_context", "Get the complete issue context required for agent work.", {"issue": ISSUE_REF}, ["issue"]),
    tool("get_available_transitions", "Get policy-aware transitions for the current issue revision.", {"issue": ISSUE_REF}, ["issue"]),
    tool("create_issue", "Create an issue using stable actor and project references.", {"project": PROJECT_REF, "title": {"type": "string", "minLength": 1, "examples": ["Implement discovery tools"]}, "type": {"type": "string", "enum": list(ISSUE_TYPES), "default": "task"}, "description": {"type": "string", "default": ""}, "priority": {"type": "string", "enum": list(PRIORITIES), "default": "medium"}, "milestone": MILESTONE_REF, "release_id": {"type": "integer"}, "assignee": ACTOR_REF, "reviewer": ACTOR_REF}, ["project", "title"]),
    tool("update_issue", "Update issue fields if expected_revision is current.", {"issue": ISSUE_REF, "expected_revision": {"type": "integer", "minimum": 1}, "title": {"type": "string", "minLength": 1}, "description": {"type": "string"}, "priority": {"type": "string", "enum": list(PRIORITIES)}, "milestone": {"type": ["string", "null"], "description": "Stable milestone key, or null to clear it."}, "release_id": {"oneOf": [{"type": "integer"}, {"type": "null"}]}, "assignee": {"type": ["string", "null"], "description": "Stable actor name, or null to unassign."}, "reviewer": {"type": ["string", "null"], "description": "Stable actor name, or null to clear the reviewer."}, "position": {"type": "number"}}, ["issue", "expected_revision"]),
    tool("transition_issue", "Move an issue through an allowed workflow transition.", {"issue": ISSUE_REF, "status": {"type": "string"}, "expected_revision": {"type": "integer", "minimum": 1}}, ["issue", "status", "expected_revision"]),
    tool("claim_issue", "Atomically claim an issue for the authenticated actor.", {"issue": ISSUE_REF, "expected_revision": {"type": "integer", "minimum": 1}, "lease_seconds": {"type": "integer", "minimum": 60, "maximum": 86400, "default": 1800}}, ["issue", "expected_revision"]),
    tool("release_issue", "Release an issue claimed by the authenticated actor.", {"issue": ISSUE_REF, "expected_revision": {"type": "integer", "minimum": 1}}, ["issue", "expected_revision"]),
    tool("create_milestone", "Create a project milestone.", {"project": PROJECT_REF, "key": {"type": "string"}, "name": {"type": "string", "minLength": 1}, "description": {"type": "string"}, "due_at": {"type": "string"}}, ["project", "name"]),
    tool("set_workflow", "Configure a manually managed workflow.", {"project": PROJECT_REF, "issue_type": {"type": "string", "enum": list(ISSUE_TYPES)}, "states": {"type": "array", "items": {"type": "string"}, "minItems": 1}, "transitions": {"type": "array", "items": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 2}},}, ["project", "issue_type", "states", "transitions"]),
    tool("add_comment", "Add a Markdown comment.", {"issue": ISSUE_REF, "body": {"type": "string", "minLength": 1}}, ["issue", "body"]),
    tool("update_comment", "Edit a comment.", {"comment": RELATED_REF, "body": {"type": "string", "minLength": 1}}, ["comment", "body"]),
    tool("delete_comment", "Delete a comment.", {"comment": RELATED_REF}, ["comment"]),
    tool("add_checklist_item", "Add a checklist item.", {"issue": ISSUE_REF, "text": {"type": "string", "minLength": 1}, "completed": {"type": "boolean", "default": False}, "position": {"type": "number", "default": 0}}, ["issue", "text"]),
    tool("update_checklist_item", "Edit a checklist item.", {"item": RELATED_REF, "text": {"type": "string", "minLength": 1}, "completed": {"type": "boolean", "default": False}}, ["item"]),
    tool("complete_checklist_item", "Atomically mark a checklist item complete.", {"item": RELATED_REF}, ["item"]),
    tool("delete_checklist_item", "Delete a checklist item.", {"item": RELATED_REF}, ["item"]),
    tool("add_dependency", "Add a blocking or related issue dependency.", {"issue": ISSUE_REF, "depends_on": ISSUE_REF, "relation": {"type": "string", "enum": ["blocks", "related"], "default": "blocks"}}, ["issue", "depends_on"]),
    tool("remove_dependency", "Remove an issue dependency.", {"issue": ISSUE_REF, "depends_on": ISSUE_REF, "relation": {"type": "string", "enum": ["blocks", "related"], "default": "blocks"}}, ["issue", "depends_on"]),
    tool("update_dependency", "Change the kind of an existing dependency.", {"issue": ISSUE_REF, "depends_on": ISSUE_REF, "relation": {"type": "string", "enum": ["blocks", "related"], "default": "blocks"}, "new_relation": {"type": "string", "enum": ["blocks", "related"]}}, ["issue", "depends_on", "new_relation"]),
    tool("add_attachment", "Attach a repository-local file reference.", {"issue": ISSUE_REF, "name": {"type": "string"}, "path": {"type": "string"}, "media_type": {"type": "string"}}, ["issue", "name", "path"]),
    tool("update_attachment", "Update an attachment reference.", {"attachment": RELATED_REF, "name": {"type": "string", "minLength": 1}, "path": {"type": "string", "minLength": 1}, "media_type": {"type": "string"}}, ["attachment"]),
    tool("delete_attachment", "Delete an attachment reference.", {"attachment": RELATED_REF}, ["attachment"]),
    tool("create_label", "Create a label in a manually managed project.", {"project": PROJECT_REF, "key": {"type": "string"}, "name": {"type": "string"}, "color": {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$", "default": "#64748b"}}, ["project", "name"]),
    tool("update_label", "Update a manually managed label by stable key.", {"project": PROJECT_REF, "label": LABEL_REF, "name": {"type": "string", "minLength": 1}, "color": {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$"}}, ["project", "label"]),
    tool("delete_label", "Delete a manually managed label by stable key.", {"project": PROJECT_REF, "label": LABEL_REF}, ["project", "label"]),
    tool("add_label", "Attach a project label by stable key.", {"issue": ISSUE_REF, "label": LABEL_REF}, ["issue", "label"]),
    tool("remove_label", "Remove a label from an issue by stable key.", {"issue": ISSUE_REF, "label": LABEL_REF}, ["issue", "label"]),
    tool("add_git_link", "Associate a branch, commit, PR, or MR.", {"issue": ISSUE_REF, "link_kind": {"type": "string", "enum": ["branch", "commit", "pr", "mr"], "default": "branch"}, "ref": {"type": "string"}, "url": {"type": "string"}}, ["issue", "ref"]),
    tool("update_git_link", "Update a Git link.", {"link": RELATED_REF, "link_kind": {"type": "string", "enum": ["branch", "commit", "pr", "mr"]}, "ref": {"type": "string", "minLength": 1}, "url": {"type": "string"}}, ["link"]),
    tool("delete_git_link", "Delete a Git link.", {"link": RELATED_REF}, ["link"]),
    tool("list_activity", "Read activity entries.", {"entity_type": {"type": "string"}, "entity_id": {"type": "integer"}, "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100}}),
]

READ_TOOLS = {"whoami", "list_actors", "list_projects", "get_project_context", "list_workflows", "get_workflow", "list_labels", "get_label", "list_milestones", "get_milestone", "list_releases", "list_issues", "get_issue_context", "get_available_transitions", "list_activity"}
ADMIN_TOOLS = {"create_actor", "rotate_actor_token", "set_actor_role"}


def schemas(role: str | None = None) -> list[dict[str, Any]]:
    """Return self-documenting JSON Schemas, including stable domain errors."""
    tools = copy.deepcopy(TOOLS)
    for item in tools:
        schema = item["inputSchema"]
        schema["description"] = f"Arguments for {item['name']}."
        schema["examples"] = [{}] if not schema["required"] else [
            {name: prop.get("examples", [prop.get("default")])[0] for name, prop in schema["properties"].items() if name in schema["required"] and (prop.get("examples") or "default" in prop)}
        ]
        for name, prop in schema["properties"].items():
            prop.setdefault("description", name.replace("_", " ").capitalize() + ".")
            if prop.get("type") == "array":
                prop.setdefault("items", {})
            if "examples" not in prop and "default" in prop:
                prop["examples"] = [prop["default"]]
        item["x-errorResponses"] = copy.deepcopy(ERROR_RESPONSES)
        item["outputSchema"] = {
            "description": "Tool result, or a documented domain error when isError is true.",
            "oneOf": [
                {},
                {"type": "object", "required": ["error"], "properties": {"error": {"type": "object", "required": ["code", "message", "retryable"], "properties": {"code": {"type": "string", "enum": list(ERROR_RESPONSES)}, "message": {"type": "string"}, "retryable": {"type": "boolean"}}}}},
            ],
        }
    if role == "viewer":
        return [item for item in tools if item["name"] in READ_TOOLS]
    if role == "member":
        return [item for item in tools if item["name"] not in ADMIN_TOOLS]
    return tools


def _actor_id(board: Board, value: Any) -> int | None:
    return None if value is None else board.get_actor(value)["id"]


def _label_id(board: Board, issue_id: int, value: int | str) -> int:
    if isinstance(value, int): return value
    issue = board.get_issue(issue_id)
    context = board.project_context(issue["project_id"])
    found = next((label for label in context["labels"] if label.get("key") == value or label["name"] == value), None)
    if not found: raise KeyError("label not found")
    return found["id"]


def _related_id(value: int | str, expected_kind: str) -> int:
    """Resolve an opaque related-object key; integers remain a legacy internal API."""
    if isinstance(value, int):
        return value
    try:
        _, kind, raw_id = value.rsplit(":", 2)
        if kind != expected_kind:
            raise ValueError
        return int(raw_id)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid {expected_kind} key") from exc


def _with_related_keys(value: Any) -> Any:
    """Add stable opaque keys to issue-related records returned through MCP."""
    if not isinstance(value, dict) or "identifier" not in value:
        return value
    identifier = value["identifier"]
    for collection, kind in (("comments", "comment"), ("checklist", "checklist"), ("attachments", "attachment"), ("git_links", "git")):
        for record in value.get(collection, []):
            if "id" in record:
                record["key"] = f"{identifier}:{kind}:{record['id']}"
    return value


def call_tool(board: Board, actor: int, name: str, args: dict[str, Any]) -> Any:
    args = dict(args)
    identity = board.get_actor(actor)
    if name not in READ_TOOLS and identity["role"] == "viewer":
        raise AuthorizationError("viewer role is read-only")
    if name == "create_actor": return board.provision_actor(actor, **args)
    if name == "rotate_actor_token": return board.rotate_actor_token(actor, args["actor"])
    if name == "set_actor_role": return board.set_actor_role(actor, args["actor"], args["role"])
    if name == "whoami": return board.get_actor(actor)
    if name == "list_actors": return board.list_actors()
    if name == "list_projects": return board.list_projects()
    if name == "get_project_context": return board.project_context(args["project"])
    if name in {"list_workflows", "list_labels", "list_milestones", "list_releases"}: return board.project_context(args["project"])[name.removeprefix("list_")]
    if name == "get_workflow": return board.get_workflow(args["project"], args["issue_type"])
    if name == "get_milestone": return board.get_milestone(args["project"], args["milestone"])
    if name == "get_label": return board.get_label(args["project"], args["label"])
    if name == "create_release": args["project_id"] = board.resolve_project(args.pop("project")); return board.create_release(actor, **args)
    if name == "transition_release": return board.transition_release(actor, **args)
    if name == "create_project": return board.create_project(actor, **args)
    if name == "list_issues":
        if "project" in args: args["project_id"] = board.resolve_project(args.pop("project"))
        return board.list_issues(**args)
    if name == "get_issue_context": return _with_related_keys(board.get_issue_context(args["issue"]))
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
    related_operations = {
        "update_comment": ("comment", "comment_id"), "delete_comment": ("comment", "comment_id"),
        "update_checklist_item": ("checklist", "item_id"), "complete_checklist_item": ("checklist", "item_id"),
        "delete_checklist_item": ("checklist", "item_id"), "update_attachment": ("attachment", "attachment_id"),
        "delete_attachment": ("attachment", "attachment_id"), "update_git_link": ("git", "link_id"),
        "delete_git_link": ("git", "link_id"),
    }
    if name in related_operations:
        kind, internal_name = related_operations[name]
        public_name = {"comment": "comment", "checklist": "item", "attachment": "attachment", "git": "link"}[kind]
        raw = args.pop(public_name, args.pop(internal_name, None))
        if raw is None: raise ValueError(f"{public_name} is required")
        args[internal_name] = _related_id(raw, kind)
        if name == "update_git_link" and "link_kind" in args: args["kind"] = args.pop("link_kind")
        return getattr(board, name)(actor, **args)
    if name == "remove_dependency": return board.remove_dependency(actor, board.resolve_issue(args["issue"]), board.resolve_issue(args["depends_on"]), args.get("relation", "blocks"))
    if name == "update_dependency": return board.update_dependency(actor, board.resolve_issue(args["issue"]), board.resolve_issue(args["depends_on"]), args.get("relation", "blocks"), args["new_relation"])
    if name == "create_label": args["project_id"] = board.resolve_project(args.pop("project")); return board.create_label(actor, **args)
    if name in {"update_label", "delete_label"}:
        project_id = board.resolve_project(args.pop("project")); key = args.pop("label")
        return getattr(board, name)(actor, project_id, key, **args)
    if name in {"add_label", "remove_label"}:
        issue_id = board.resolve_issue(args["issue"]); label_id = _label_id(board, issue_id, args["label"]); return getattr(board, name)(actor, issue_id, label_id)
    if name == "list_activity": return board.activity(**args)
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
        result = {"protocolVersion": "2025-03-26", "capabilities": {"tools": {"listChanged": False}}, "serverInfo": {"name": "local-board", "version": __version__}}
    elif method == "ping": result = {}
    elif method == "tools/list": result = {"tools": schemas(board.get_actor(actor)["role"])}
    elif method == "tools/call":
        params = request.get("params", {})
        try:
            value = call_tool(board, actor, params["name"], params.get("arguments", {}))
            result = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, default=str)}], "structuredContent": value}
        except (AuthorizationError, ConflictError, DatabaseBusyError, InvalidTransitionError, ValueError, KeyError, TypeError) as exc:
            message = str(exc).strip("'")
            if isinstance(exc, AuthorizationError): code = "unauthorized"
            elif isinstance(exc, ConflictError): code = "conflict"
            elif isinstance(exc, DatabaseBusyError): code = "retryable"
            elif isinstance(exc, InvalidTransitionError): code = "invalid_transition"
            elif isinstance(exc, KeyError): code = "not_found"
            elif "blocked" in message or "claimed or assigned" in message: code = "blocked"
            else: code = "invalid_request"
            error = {"code": code, "message": message, "retryable": isinstance(exc, (ConflictError, DatabaseBusyError))}
            result = {"content": [{"type": "text", "text": json.dumps(error)}], "structuredContent": {"error": error}, "isError": True}
    else:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}}
    return {"jsonrpc": "2.0", "id": request_id, "result": result}
