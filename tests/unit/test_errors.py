"""The transport-shared error contract: every exception maps to a structured response."""

import json
import sqlite3
import unittest

from local_board.db import AuthorizationError, ConflictError, DatabaseBusyError
from local_board.errors import describe, error_body


class DescribeTest(unittest.TestCase):
    def check(self, exc, status, code, retryable):
        got_status, got_code, _, got_retryable = describe(exc)
        self.assertEqual((got_status, got_code, got_retryable), (status, code, retryable))

    def test_domain_exceptions(self):
        self.check(ConflictError("stale"), 409, "conflict", True)
        self.check(AuthorizationError("no"), 403, "unauthorized", False)
        self.check(DatabaseBusyError("locked"), 503, "retryable", True)
        self.check(KeyError("issue not found"), 404, "not_found", False)
        self.check(ValueError("bad"), 400, "invalid_request", False)
        self.check(TypeError("bad type"), 400, "invalid_request", False)

    def test_policy_message_maps_to_blocked(self):
        self.check(ValueError("issue must be claimed or assigned before it starts"), 400, "blocked", False)

    def test_json_decode_error_is_invalid_request(self):
        try:
            json.loads("{broken")
        except json.JSONDecodeError as exc:
            self.check(exc, 400, "invalid_request", False)

    def test_unknown_exception_is_internal_500(self):
        status, code, message, retryable = describe(RuntimeError("boom"))
        self.assertEqual((status, code, retryable), (500, "internal", False))
        self.assertNotIn("boom", message)

    def test_unique_constraint_translations(self):
        cases = {
            "actors.name": "actor name already exists",
            "labels.name": "label name already exists",
            "labels.key": "label key already exists",
            "milestones.key": "milestone key already exists",
            "milestones.name": "milestone name already exists",
            "statuses.name": "status name already exists",
            "issues.number": "issue number already exists",
        }
        for column, expected in cases.items():
            exc = sqlite3.IntegrityError(f"UNIQUE constraint failed: {column}")
            status, code, message, retryable = describe(exc)
            self.assertEqual((status, code, message, retryable), (409, "conflict", expected, False),
                             column)

    def test_foreign_key_violation_is_invalid_request(self):
        exc = sqlite3.IntegrityError("FOREIGN KEY constraint failed")
        status, code, message, _ = describe(exc)
        self.assertEqual((status, code), (400, "invalid_request"))
        self.assertIn("does not exist", message)

    def test_unrecognized_integrity_error_falls_back_to_conflict(self):
        exc = sqlite3.IntegrityError("CHECK constraint failed: something")
        status, code, _, _ = describe(exc)
        self.assertEqual((status, code), (409, "conflict"))

    def test_error_body_shape(self):
        body = error_body(ConflictError("stale revision"))
        self.assertEqual(set(body), {"error"})
        self.assertEqual(set(body["error"]), {"code", "message", "retryable"})
        self.assertEqual(body["error"]["code"], "conflict")


if __name__ == "__main__":
    unittest.main()
