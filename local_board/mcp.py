"""MCP (JSON-RPC 2.0) transport over the clean-board domain in local_board.db.

One board, one prefix, fixed-category statuses, free transitions. This module only
adapts JSON-RPC requests and MCP tool arguments onto local_board.db.Board — all
domain rules (revisions, cycles, roles) live there.
"""

from __future__ import annotations

import difflib
import json
import re
from typing import Any, Callable

from . import __version__
from .db import AuthorizationError, Board, GIT_LINK_KINDS, PRIORITIES
from .errors import ERROR_RESPONSES, describe


# -- shared argument schemas -------------------------------------------------------

_ISSUE_PATTERN = r"^[A-Za-z0-9]{2,10}-[1-9][0-9]*$"

ISSUE_REF = {
    "description": "Issue identifier such as APP-12, or its internal id.",
    "oneOf": [{"type": "string", "pattern": _ISSUE_PATTERN}, {"type": "integer", "minimum": 1}],
}
ISSUE_REF_OR_NULL = {
    "description": "Issue identifier such as APP-12, or null to clear it.",
    "oneOf": [
        {"type": "string", "pattern": _ISSUE_PATTERN},
        {"type": "integer", "minimum": 1},
        {"type": "null"},
    ],
}
ACTOR_REF = {"description": "Actor name or id.", "oneOf": [{"type": "string"}, {"type": "integer"}]}
ACTOR_REF_OR_NULL = {
    "description": "Actor name or id, or null to unassign.",
    "oneOf": [{"type": "string"}, {"type": "integer"}, {"type": "null"}],
}
MILESTONE_REF = {
    "description": "Milestone key, name, or id.",
    "oneOf": [{"type": "string"}, {"type": "integer"}],
}
MILESTONE_REF_OR_NULL = {
    "description": "Milestone key, name, or id, or null to clear it.",
    "oneOf": [{"type": "string"}, {"type": "integer"}, {"type": "null"}],
}
LABEL_REF = {
    "description": "Label key, name, or id.",
    "oneOf": [{"type": "string"}, {"type": "integer"}],
}
RETURN_FULL_ISSUE = {
    "type": "boolean",
    "default": False,
    "description": "Return the full issue object instead of the compact confirmation.",
}


def obj(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


def tool(
    name: str,
    description: str,
    properties: dict[str, Any] | None = None,
    required: list[str] | None = None,
) -> dict[str, Any]:
    return {"name": name, "description": description, "inputSchema": obj(properties or {}, required)}


# -- tool catalog, grouped by required role ----------------------------------------

TOOLS_READ = [
    tool("whoami", "Return the authenticated actor's identity and role."),
    tool("get_board_context", "Get board prefix, name, statuses, labels, milestones, and policy."),
    tool(
        "list_issues",
        "Search issues and return summaries.",
        {
            "status": {"type": "string", "description": "Exact status name."},
            "milestone": MILESTONE_REF,
            "assignee": ACTOR_REF,
            "label": LABEL_REF,
            "parent": ISSUE_REF,
            "query": {"type": "string", "description": "Substring match on title and description."},
        },
    ),
    tool("get_issue",
         "Get one issue with labels, comments, dependencies, children, and git links. "
         "comments_total is always present, so a trimmed thread is visible.",
         {"issue": ISSUE_REF,
          "comments": {"description": "'all' (default), 'none', or the last N comments.",
                       "oneOf": [{"type": "string", "enum": ["all", "none"]},
                                 {"type": "integer", "minimum": 1}],
                       "default": "all"}},
         ["issue"]),
    tool(
        "list_activity",
        "Read the append-only activity log.",
        {
            "entity_type": {"type": "string", "description": "e.g. issue, milestone, label, actor."},
            "entity_id": {"type": "integer", "description": "Internal id of the entity."},
            "issue": ISSUE_REF,
            "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100},
        },
    ),
]

TOOLS_WRITE = [
    tool(
        "create_issue",
        "Create an issue on the board.",
        {
            "title": {"type": "string", "minLength": 1},
            "description": {"type": "string", "description": "Markdown body.", "default": ""},
            "priority": {"type": "string", "enum": list(PRIORITIES)},
            "status": {"type": "string", "description": "Defaults to the board's initial status."},
            "milestone": MILESTONE_REF,
            "parent": ISSUE_REF,
            "assignee": ACTOR_REF,
            "labels": {"type": "array", "items": {"type": "string"}, "description": "Label keys or names."},
        },
        ["title"],
    ),
    tool(
        "update_issue",
        "Update issue fields if expected_revision is current. Advances the issue revision; "
        "returns a compact confirmation with the new revision.",
        {
            "issue": ISSUE_REF,
            "expected_revision": {"type": "integer", "minimum": 1},
            "title": {"type": "string", "minLength": 1},
            "description": {"type": "string"},
            "priority": {"type": "string", "enum": list(PRIORITIES)},
            "status": {"type": "string", "description": "Any configured status; transitions are free."},
            "assignee": ACTOR_REF_OR_NULL,
            "milestone": MILESTONE_REF_OR_NULL,
            "parent": ISSUE_REF_OR_NULL,
            "labels": {"type": "array", "items": {"type": "string"}, "description": "Replaces all labels."},
            "position": {"type": "number", "description": "Manual ordering within the status column."},
            "return_full_issue": RETURN_FULL_ISSUE,
        },
        ["issue", "expected_revision"],
    ),
    tool(
        "claim_issue",
        "Atomically claim an issue for the authenticated actor with a time-boxed lease, "
        "optionally moving it to a status in the same transaction. Advances the issue revision.",
        {
            "issue": ISSUE_REF,
            "expected_revision": {"type": "integer", "minimum": 1},
            "lease_seconds": {"type": "integer", "minimum": 60, "maximum": 86400, "default": 1800},
            "status": {"type": "string",
                       "description": "Optional status to move to atomically, e.g. In Progress."},
            "return_full_issue": RETURN_FULL_ISSUE,
        },
        ["issue", "expected_revision"],
    ),
    tool(
        "release_issue",
        "Release an issue claimed by the authenticated actor. Advances the issue revision. "
        "Not needed for finished work: completing an issue extinguishes the lease automatically.",
        {
            "issue": ISSUE_REF,
            "expected_revision": {"type": "integer", "minimum": 1},
            "return_full_issue": RETURN_FULL_ISSUE,
        },
        ["issue", "expected_revision"],
    ),
    tool("add_comment",
         "Add a Markdown comment to an issue. Does not change the issue revision. Returns the "
         "comment id and current issue_revision; the body is echoed only with return_full_comment.",
         {"issue": ISSUE_REF, "body": {"type": "string", "minLength": 1},
          "return_full_comment": {"type": "boolean", "default": False,
                                  "description": "Echo the stored comment body in the response."}},
         ["issue", "body"]),
    tool("update_comment",
         "Edit a comment. Only its author or an admin may edit it. Does not change the issue revision.",
         {"comment_id": {"type": "integer", "minimum": 1}, "body": {"type": "string", "minLength": 1}},
         ["comment_id", "body"]),
    tool("add_dependency",
         "Record that an issue is blocked by another issue. Does not change the issue revision.",
         {"issue": ISSUE_REF, "depends_on": ISSUE_REF, "return_full_issue": RETURN_FULL_ISSUE},
         ["issue", "depends_on"]),
    tool("remove_dependency",
         "Remove a blocking dependency between two issues. Does not change the issue revision.",
         {"issue": ISSUE_REF, "depends_on": ISSUE_REF, "return_full_issue": RETURN_FULL_ISSUE},
         ["issue", "depends_on"]),
    tool(
        "add_git_link",
        "Associate landing commits or a PR/MR with an issue (branches live in git via the issue-id naming convention). Does not change the issue revision. Returns the created "
        "link(s) with their ids. Pass either ref or refs (a batch, one transaction).",
        {
            "issue": ISSUE_REF,
            "ref": {"type": "string", "minLength": 1, "description": "Commit SHA or PR/MR number."},
            "refs": {"type": "array", "items": {"type": "string", "minLength": 1}, "minItems": 1,
                     "description": "Several refs of the same kind at once."},
            "kind": {"type": "string", "enum": list(GIT_LINK_KINDS), "default": "commit"},
            "url": {"type": "string"},
            "return_full_issue": RETURN_FULL_ISSUE,
        },
        ["issue"],
    ),
    tool(
        "create_milestone",
        "Create a board milestone.",
        {
            "name": {"type": "string", "minLength": 1},
            "key": {"type": "string", "description": "Stable short key, e.g. v1."},
            "description": {"type": "string"},
            "due_at": {"type": "string", "description": "ISO 8601 timestamp."},
        },
        ["name"],
    ),
    tool(
        "create_label",
        "Create a board label.",
        {
            "name": {"type": "string", "minLength": 1},
            "key": {"type": "string", "description": "Stable short key."},
            "color": {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$"},
        },
        ["name"],
    ),
]

TOOLS_CORRECTION = [
    tool("update_label", "Rename or recolor a label.",
         {"label": LABEL_REF, "name": {"type": "string", "minLength": 1},
          "color": {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$"}}, ["label"]),
    tool("delete_label", "Delete a label.", {"label": LABEL_REF}, ["label"]),
    tool(
        "update_milestone",
        "Rename, redescribe, or reschedule a milestone.",
        {
            "milestone": MILESTONE_REF,
            "name": {"type": "string", "minLength": 1},
            "description": {"type": "string"},
            "due_at": {"type": "string"},
        },
        ["milestone"],
    ),
    tool("delete_milestone", "Delete a milestone.", {"milestone": MILESTONE_REF}, ["milestone"]),
    tool("delete_comment", "Delete a comment.", {"comment_id": {"type": "integer", "minimum": 1}},
         ["comment_id"]),
    tool(
        "update_git_link",
        "Update a Git link.",
        {
            "link_id": {"type": "integer", "minimum": 1},
            "kind": {"type": "string", "enum": list(GIT_LINK_KINDS)},
            "ref": {"type": "string", "minLength": 1},
            "url": {"type": "string"},
        },
        ["link_id"],
    ),
    tool(
        "delete_git_link",
        "Delete a Git link.",
        {"link_id": {"type": "integer", "minimum": 1}},
        ["link_id"],
    ),
]

TOOLS_ADMIN = [
    tool(
        "create_actor",
        "Provision a new actor and return its bearer token once. Admin only.",
        {
            "name": {"type": "string", "minLength": 1},
            "kind": {"type": "string", "enum": ["agent", "human"], "default": "agent"},
            "role": {"type": "string", "enum": ["member", "viewer", "admin"], "default": "member"},
        },
        ["name"],
    ),
    tool("rotate_actor_token", "Invalidate an actor's token and return its replacement once. Admin only.",
         {"actor": ACTOR_REF}, ["actor"]),
    tool("set_actor_role", "Change an actor's role. Admin only.",
         {"actor": ACTOR_REF, "role": {"type": "string", "enum": ["admin", "member", "viewer"]}},
         ["actor", "role"]),
]

# Annotations for the generated cheat-sheet (references/tools.md, rendered by
# onboarding.render_tools_reference from this catalog — the single source both
# tools/list and the skill reference are built from). They are kept out of the
# tool dicts so tools/list serves exactly the MCP schema and nothing more.
# "+" advances the issue's revision, "=" leaves it unchanged; read tools and
# board-level tools carry no marker.
TOOL_REV = {
    "create_issue": "+",
    "update_issue": "+",
    "claim_issue": "+",
    "release_issue": "+",
    "add_comment": "=",
    "update_comment": "=",
    "add_dependency": "=",
    "remove_dependency": "=",
    "add_git_link": "=",
}

TOOL_NOTES = {
    "list_activity": "`issue` accepts an APP-12 identifier",
    "get_issue": 'comments: "all" (default) | "none" | N for the last N; response always carries `comments_total`',
    "create_issue": "new issue",
    "add_comment": "returns `comment_id` and `issue_revision`; full body only with the flag",
    "add_git_link": (
        "pass `ref` or `refs`; kind: commit (default) | pr | mr; returns the created link(s) with their ids. "
        "Branches are not linked: the issue id in the branch name is the association, inside git itself"
    ),
}

READ_TOOLS = {item["name"] for item in TOOLS_READ}
WRITE_TOOLS = {item["name"] for item in TOOLS_WRITE}
CORRECTION_TOOLS = {item["name"] for item in TOOLS_CORRECTION}
ADMIN_TOOLS = {item["name"] for item in TOOLS_ADMIN}

_ALL_TOOLS = TOOLS_READ + TOOLS_WRITE + TOOLS_CORRECTION + TOOLS_ADMIN
for _entry in _ALL_TOOLS:
    _entry["x-errorResponses"] = ERROR_RESPONSES

_ROLE_TOOLS = {
    "viewer": TOOLS_READ,
    "member": TOOLS_READ + TOOLS_WRITE,
    "admin": _ALL_TOOLS,
}


def schemas(role: str | None = None) -> list[dict[str, Any]]:
    """Return the tool list available to a role. Built once at import; never copied."""
    return _ROLE_TOOLS.get(role, _ALL_TOOLS)


# -- argument validation -------------------------------------------------------------

_TOOL_SCHEMAS = {item["name"]: item["inputSchema"] for item in _ALL_TOOLS}


def _type_matches(expected: str, value: Any) -> bool:
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "string":
        return isinstance(value, str)
    if expected == "array":
        return isinstance(value, list)
    if expected == "null":
        return value is None
    return True


def _value_matches(schema: dict[str, Any], value: Any) -> bool:
    if "oneOf" in schema:
        return any(_value_matches(branch, value) for branch in schema["oneOf"])
    expected = schema.get("type")
    if expected is not None and not _type_matches(expected, value):
        return False
    if "enum" in schema and value not in schema["enum"]:
        return False
    if isinstance(value, str):
        if "pattern" in schema and not re.fullmatch(schema["pattern"], value):
            return False
        if len(value) < schema.get("minLength", 0):
            return False
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            return False
        if "maximum" in schema and value > schema["maximum"]:
            return False
    if isinstance(value, list) and "items" in schema:
        return all(_value_matches(schema["items"], item) for item in value)
    return True


def _expectation(schema: dict[str, Any]) -> str:
    if "enum" in schema:
        return "one of " + ", ".join(map(str, schema["enum"]))
    if "oneOf" in schema:
        return " or ".join(_expectation(branch) for branch in schema["oneOf"])
    parts = [schema.get("type", "value")]
    if "pattern" in schema:
        parts.append(f"matching {schema['pattern']}")
    if "minimum" in schema:
        parts.append(f">= {schema['minimum']}")
    return " ".join(parts)


def validate_arguments(name: str, args: dict[str, Any]) -> None:
    """Enforce the published inputSchema so a typo is invalid_request, not a fake not_found."""
    schema = _TOOL_SCHEMAS[name]
    properties = schema["properties"]
    for field in args:
        if field not in properties:
            message = f"unknown field '{field}' for {name}"
            close = difflib.get_close_matches(field, properties, n=1)
            if close:
                message += f"; did you mean '{close[0]}'?"
            else:
                message += f"; valid fields: {', '.join(sorted(properties))}"
            raise ValueError(message)
    for field in schema["required"]:
        if field not in args:
            raise ValueError(f"missing required field '{field}' for {name}")
    for field, value in args.items():
        if not _value_matches(properties[field], value):
            raise ValueError(
                f"invalid value for field '{field}': expected {_expectation(properties[field])}"
            )


# -- reference resolution -----------------------------------------------------------

def _resolve_actor(board: Board, value: int | str) -> int:
    return board.get_actor(value)["id"]


def _resolve_milestone(board: Board, value: int | str) -> int:
    if isinstance(value, int):
        return value
    for milestone in board.board_context()["milestones"]:
        if milestone.get("key") == value or milestone["name"] == value:
            return milestone["id"]
    raise KeyError("milestone not found")


# -- tool handlers --------------------------------------------------------------------

def _whoami(board: Board, actor: int, args: dict[str, Any]) -> Any:
    return board.get_actor(actor)


def _get_board_context(board: Board, actor: int, args: dict[str, Any]) -> Any:
    return board.board_context()


def _list_issues(board: Board, actor: int, args: dict[str, Any]) -> Any:
    milestone_id = _resolve_milestone(board, args["milestone"]) if "milestone" in args else None
    assignee_id = _resolve_actor(board, args["assignee"]) if "assignee" in args else None
    parent_id = board.resolve_issue(args["parent"]) if "parent" in args else None
    return board.list_issues(
        status=args.get("status"),
        milestone_id=milestone_id,
        assignee_id=assignee_id,
        label=args.get("label"),
        parent_id=parent_id,
        query=args.get("query"),
    )


def _get_issue(board: Board, actor: int, args: dict[str, Any]) -> Any:
    window = args.get("comments", "all")
    if window == "all":
        limit = None
    elif window == "none":
        limit = 0
    else:
        limit = int(window)
    return board.get_issue(board.resolve_issue(args["issue"]), comments_limit=limit)


def _list_activity(board: Board, actor: int, args: dict[str, Any]) -> Any:
    entity_type = args.get("entity_type")
    entity_id = args.get("entity_id")
    if "issue" in args:
        entity_type = "issue"
        entity_id = board.resolve_issue(args["issue"])
    return board.activity(entity_type=entity_type, entity_id=entity_id, limit=args.get("limit", 100))


def _create_issue(board: Board, actor: int, args: dict[str, Any]) -> Any:
    milestone_id = _resolve_milestone(board, args["milestone"]) if "milestone" in args else None
    parent_id = board.resolve_issue(args["parent"]) if "parent" in args else None
    assignee_id = _resolve_actor(board, args["assignee"]) if "assignee" in args else None
    return board.create_issue(
        actor,
        args["title"],
        args.get("description", ""),
        priority=args.get("priority"),
        status=args.get("status"),
        milestone_id=milestone_id,
        parent_id=parent_id,
        assignee_id=assignee_id,
        labels=args.get("labels"),
    )


def _confirmation(issue: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    """Compact mutation acknowledgement; the full object costs return_full_issue: true."""
    if args.get("return_full_issue"):
        return issue
    keys = ("identifier", "revision", "status", "category", "blocked", "assignee", "claim_expires_at")
    result = {key: issue.get(key) for key in keys}
    if "lease_revoked_from" in issue:
        result["lease_revoked_from"] = issue["lease_revoked_from"]
    return result


def _update_issue(board: Board, actor: int, args: dict[str, Any]) -> Any:
    issue_id = board.resolve_issue(args["issue"])
    fields: dict[str, Any] = {"expected_revision": args["expected_revision"]}
    for field in ("title", "description", "priority", "status", "position"):
        if field in args:
            fields[field] = args[field]
    if "assignee" in args:
        value = args["assignee"]
        fields["assignee_id"] = None if value is None else _resolve_actor(board, value)
    if "milestone" in args:
        value = args["milestone"]
        fields["milestone_id"] = None if value is None else _resolve_milestone(board, value)
    if "parent" in args:
        value = args["parent"]
        fields["parent_id"] = None if value is None else board.resolve_issue(value)
    if "labels" in args:
        fields["labels"] = args["labels"]
    return _confirmation(board.update_issue(actor, issue_id, **fields), args)


def _claim_issue(board: Board, actor: int, args: dict[str, Any]) -> Any:
    issue_id = board.resolve_issue(args["issue"])
    issue = board.claim_issue(
        actor, issue_id, args["expected_revision"], args.get("lease_seconds", 1800),
        status=args.get("status"),
    )
    return _confirmation(issue, args)


def _release_issue(board: Board, actor: int, args: dict[str, Any]) -> Any:
    issue_id = board.resolve_issue(args["issue"])
    return _confirmation(board.release_issue(actor, issue_id, args["expected_revision"]), args)


def _add_comment(board: Board, actor: int, args: dict[str, Any]) -> Any:
    issue_id = board.resolve_issue(args["issue"])
    comment = board.add_comment(actor, issue_id, args["body"])
    if args.get("return_full_comment"):
        return comment
    keys = ("id", "issue_id", "issue_revision", "created_at")
    return {key: comment[key] for key in keys}


def _update_comment(board: Board, actor: int, args: dict[str, Any]) -> Any:
    return board.update_comment(actor, args["comment_id"], args["body"])


def _delete_comment(board: Board, actor: int, args: dict[str, Any]) -> Any:
    return board.delete_comment(actor, args["comment_id"])


def _add_dependency(board: Board, actor: int, args: dict[str, Any]) -> Any:
    issue_id = board.resolve_issue(args["issue"])
    depends_on_id = board.resolve_issue(args["depends_on"])
    return _confirmation(board.add_dependency(actor, issue_id, depends_on_id), args)


def _remove_dependency(board: Board, actor: int, args: dict[str, Any]) -> Any:
    issue_id = board.resolve_issue(args["issue"])
    depends_on_id = board.resolve_issue(args["depends_on"])
    return _confirmation(board.remove_dependency(actor, issue_id, depends_on_id), args)


def _add_git_link(board: Board, actor: int, args: dict[str, Any]) -> Any:
    issue_id = board.resolve_issue(args["issue"])
    single = args.get("ref")
    batch = args.get("refs")
    if bool(single) == bool(batch):
        raise ValueError("provide exactly one of 'ref' or 'refs'")
    links = board.add_git_links(actor, issue_id, batch or [single],
                                args.get("kind", "commit"), args.get("url"))
    if args.get("return_full_issue"):
        return board.get_issue(issue_id)
    if single:
        return links[0]
    return {"issue": links[0]["issue"], "links": links}


def _update_git_link(board: Board, actor: int, args: dict[str, Any]) -> Any:
    changes = {field: args[field] for field in ("kind", "ref", "url") if field in args}
    return board.update_git_link(actor, args["link_id"], **changes)


def _delete_git_link(board: Board, actor: int, args: dict[str, Any]) -> Any:
    return board.delete_git_link(actor, args["link_id"])


def _create_milestone(board: Board, actor: int, args: dict[str, Any]) -> Any:
    return board.create_milestone(
        actor, args["name"], args.get("description", ""), args.get("due_at"), args.get("key")
    )


def _update_milestone(board: Board, actor: int, args: dict[str, Any]) -> Any:
    changes = {field: args[field] for field in ("name", "description", "due_at") if field in args}
    return board.update_milestone(actor, args["milestone"], **changes)


def _delete_milestone(board: Board, actor: int, args: dict[str, Any]) -> Any:
    return board.delete_milestone(actor, args["milestone"])


def _create_label(board: Board, actor: int, args: dict[str, Any]) -> Any:
    return board.create_label(actor, args["name"], args.get("color", "#64748b"), args.get("key"))


def _update_label(board: Board, actor: int, args: dict[str, Any]) -> Any:
    changes = {field: args[field] for field in ("name", "color") if field in args}
    return board.update_label(actor, args["label"], **changes)


def _delete_label(board: Board, actor: int, args: dict[str, Any]) -> Any:
    return board.delete_label(actor, args["label"])


def _create_actor(board: Board, actor: int, args: dict[str, Any]) -> Any:
    return board.provision_actor(actor, args["name"], args.get("kind", "agent"), args.get("role", "member"))


def _rotate_actor_token(board: Board, actor: int, args: dict[str, Any]) -> Any:
    return board.rotate_actor_token(actor, args["actor"])


def _set_actor_role(board: Board, actor: int, args: dict[str, Any]) -> Any:
    return board.set_actor_role(actor, args["actor"], args["role"])


_HANDLERS: dict[str, Callable[[Board, int, dict[str, Any]], Any]] = {
    "whoami": _whoami,
    "get_board_context": _get_board_context,
    "list_issues": _list_issues,
    "get_issue": _get_issue,
    "list_activity": _list_activity,
    "create_issue": _create_issue,
    "update_issue": _update_issue,
    "claim_issue": _claim_issue,
    "release_issue": _release_issue,
    "add_comment": _add_comment,
    "update_comment": _update_comment,
    "add_dependency": _add_dependency,
    "remove_dependency": _remove_dependency,
    "add_git_link": _add_git_link,
    "create_milestone": _create_milestone,
    "create_label": _create_label,
    "update_label": _update_label,
    "delete_label": _delete_label,
    "update_milestone": _update_milestone,
    "delete_milestone": _delete_milestone,
    "delete_comment": _delete_comment,
    "update_git_link": _update_git_link,
    "delete_git_link": _delete_git_link,
    "create_actor": _create_actor,
    "rotate_actor_token": _rotate_actor_token,
    "set_actor_role": _set_actor_role,
}


def call_tool(board: Board, actor: int, name: str, arguments: dict[str, Any]) -> Any:
    args = dict(arguments)
    identity = board.get_actor(actor)
    if identity["role"] == "viewer" and name not in READ_TOOLS:
        raise AuthorizationError("viewer role is read-only")
    if identity["role"] == "member" and (name in CORRECTION_TOOLS or name in ADMIN_TOOLS):
        raise AuthorizationError("this operation requires the admin role")
    handler = _HANDLERS.get(name)
    if handler is None:
        raise KeyError(f"unknown tool: {name}")
    validate_arguments(name, args)
    return handler(board, actor, args)


# -- connection instructions ---------------------------------------------------------

def _instructions(board: Board, actor: int) -> str:
    """A compact plain-text briefing replacing separate whoami/context startup calls."""
    identity = board.get_actor(actor)
    try:
        context = board.board_context()
    except KeyError:
        return (
            f"You are {identity['name']}, role {identity['role']}. "
            "This board has not been configured yet; run `local-board init` first."
        )
    statuses = ", ".join(f"{status['name']} ({status['category']})" for status in context["statuses"])
    label_keys = ", ".join((label["key"] or label["name"]) for label in context["labels"]) or "none defined"
    milestone_keys = ", ".join(
        (milestone["key"] or milestone["name"]) for milestone in context["milestones"]
    ) or "none defined"
    lines = [
        f"You are {identity['name']}, role {identity['role']}, on board "
        f"{context['prefix']} ({context['name']}).",
        f"Statuses in order: {statuses}.",
        f"Label keys: {label_keys}.",
        f"Milestone keys: {milestone_keys}.",
        f"Agent policy: {json.dumps(context['agent_policy'], ensure_ascii=False)}.",
        "Transitions are free. 'blocked' is advisory. "
        "Pass expected_revision from your latest read on every mutation.",
    ]
    return "\n".join(lines)


def _call_tool_result(
    board: Board, actor: int, name: str | None, arguments: dict[str, Any]
) -> dict[str, Any]:
    try:
        value = call_tool(board, actor, name, arguments)
        return {
            "content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, default=str)}],
            "structuredContent": value,
        }
    except Exception as exc:
        status, code, message, retryable = describe(exc)
        error = {"code": code, "message": message, "retryable": retryable}
        return {
            "content": [{"type": "text", "text": json.dumps(error, ensure_ascii=False, default=str)}],
            "structuredContent": {"error": error},
            "isError": True,
        }


def handle(board: Board, actor: int, request: dict[str, Any]) -> dict[str, Any] | None:
    request_id = request.get("id")
    method = request.get("method")
    if method == "notifications/initialized":
        return None
    if method == "initialize":
        result = {
            "protocolVersion": "2025-03-26",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "local-board", "version": __version__},
            "instructions": _instructions(board, actor),
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        role = board.get_actor(actor)["role"]
        result = {"tools": schemas(role)}
    elif method == "tools/call":
        params = request.get("params", {})
        result = _call_tool_result(board, actor, params.get("name"), params.get("arguments", {}))
    else:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}}
    return {"jsonrpc": "2.0", "id": request_id, "result": result}
