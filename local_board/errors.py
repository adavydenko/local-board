"""Single domain-error contract shared by the REST and MCP transports."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from .db import AuthorizationError, ConflictError, DatabaseBusyError


ERROR_RESPONSES = {
    "not_found": "The identifier or key does not exist.",
    "conflict": "The supplied revision is stale or the write conflicts with current state.",
    "blocked": "A policy condition prevents the operation.",
    "unauthorized": "The authenticated actor is not allowed to perform the operation.",
    "retryable": "A transient storage failure may succeed when retried.",
    "invalid_request": "The arguments are malformed or violate a constraint.",
    "internal": "An unexpected server error occurred.",
}

_UNIQUE_MESSAGES = {
    "issues.number": "issue number already exists",
    "labels.name": "label name already exists",
    "labels.key": "label key already exists",
    "milestones.key": "milestone key already exists",
    "milestones.name": "milestone name already exists",
    "actors.name": "actor name already exists",
    "statuses.name": "status name already exists",
}


def _integrity_message(error: sqlite3.IntegrityError) -> str:
    text = str(error)
    for column, message in _UNIQUE_MESSAGES.items():
        if column in text:
            return message
    if "FOREIGN KEY" in text:
        return "a referenced record does not exist"
    return text


def describe(exc: Exception) -> tuple[int, str, str, bool]:
    """Map an exception to (http_status, code, message, retryable)."""
    if isinstance(exc, ConflictError):
        return 409, "conflict", str(exc).strip("'"), True
    if isinstance(exc, AuthorizationError):
        return 403, "unauthorized", str(exc).strip("'"), False
    if isinstance(exc, DatabaseBusyError):
        return 503, "retryable", str(exc).strip("'"), True
    if isinstance(exc, sqlite3.IntegrityError):
        message = _integrity_message(exc)
        if "does not exist" in message:
            return 400, "invalid_request", message, False
        return 409, "conflict", message, False
    if isinstance(exc, KeyError):
        return 404, "not_found", str(exc).strip("'"), False
    if isinstance(exc, (ValueError, TypeError, json.JSONDecodeError)):
        message = str(exc).strip("'")
        code = "blocked" if "blocked" in message or "claimed or assigned" in message else "invalid_request"
        return 400, code, message, False
    return 500, "internal", "internal server error", False


def error_body(exc: Exception) -> dict[str, Any]:
    _, code, message, retryable = describe(exc)
    return {"error": {"code": code, "message": message, "retryable": retryable}}
