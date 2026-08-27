import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from local_board import __version__
from local_board.config import ConfigService, default_config, load_config
from local_board.db import Board
from local_board.web import make_handler


class WebUiMarkupTest(unittest.TestCase):
    """Small product-contract checks for the dependency-free browser client."""

    @classmethod
    def setUpClass(cls):
        cls.html = (Path(__file__).parents[2] / "local_board" / "static" / "index.html").read_text()

    def test_issue_workspace_defaults_to_a_grouped_list_with_optional_board_view(self):
        self.assertIn('id="issueList"', self.html)
        self.assertIn('data-layout="list"', self.html)
        self.assertIn('data-layout="board"', self.html)
        self.assertIn("let currentLayout='list'", self.html)

    def test_issue_detail_is_an_in_app_page_not_a_modal(self):
        self.assertIn('id="issueView"', self.html)
        self.assertIn('data-action="back-to-issues"', self.html)
        self.assertNotIn('id="detailDialog"', self.html)
        self.assertNotIn('detailDialog.showModal()', self.html)

    def test_checklist_ui_is_not_promoted_by_the_web_client(self):
        self.assertNotIn('for checklist items', self.html)
        self.assertNotIn('class="check', self.html)
        self.assertNotIn('type="checkbox" checked disabled', self.html)

    def test_primary_navigation_and_status_messages_are_accessible(self):
        self.assertIn('href="#mainContent"', self.html)
        self.assertIn('aria-label="Primary navigation"', self.html)
        self.assertIn('role="status" aria-live="polite"', self.html)
        self.assertIn('aria-label="Issue layout"', self.html)

    def test_ui_uses_an_embedded_favicon_without_an_authenticated_request(self):
        self.assertIn('<link rel="icon" href="data:,">', self.html)

    def test_new_issue_can_be_assigned_when_created_in_a_started_status(self):
        self.assertIn('id="issueAssignee"', self.html)
        self.assertIn('assignee_id:assignee?+assignee:null', self.html)

    def test_initial_restore_does_not_steal_focus_from_the_skip_link(self):
        self.assertIn("{updateHistory:false,focusContent:false}", self.html)

    def test_active_primary_navigation_exposes_the_current_page(self):
        self.assertIn("setAttribute('aria-current',active?'page':'false')", self.html)

    def test_external_git_links_reject_unsafe_url_schemes(self):
        self.assertIn("function safeExternalUrl(value)", self.html)
        self.assertIn("['http:','https:'].includes(url.protocol)", self.html)

    def test_history_restores_views_and_direct_issue_links_stay_in_the_app(self):
        self.assertIn("history.state?.fromApp", self.html)
        self.assertIn("state?.view==='activity'", self.html)
        self.assertIn("history.replaceState({view:'issues'}", self.html)

    def test_claim_conflicts_reload_current_issue_state(self):
        self.assertIn("async function mutateClaim(action,message)", self.html)
        self.assertIn("await refreshDetail()", self.html)

    def test_mobile_keeps_the_primary_filters_available(self):
        self.assertIn("#milestoneFilter{grid-column:1}", self.html)
        self.assertIn("#assigneeFilter{grid-column:2}", self.html)
        self.assertNotIn(".toolbar select{display:none}", self.html)

    def test_viewer_role_gets_a_read_only_issue_workspace(self):
        self.assertIn("function canWrite(){return identity?.role!=='viewer'}", self.html)
        self.assertIn("if(!canWrite())return readOnlyProperties(issue)", self.html)

    def test_settings_overview_exposes_repository_managed_configuration(self):
        self.assertIn('id="settingsView"', self.html)
        self.assertIn('Managed by <code>.local-board/project.toml</code>', self.html)
        self.assertIn('id="configPreview"', self.html)
        self.assertIn('function projectToml()', self.html)

    def test_settings_is_a_first_class_navigation_and_history_view(self):
        self.assertIn('data-view="settings"', self.html)
        self.assertIn("settingsView.classList.toggle('hidden',view!=='settings')", self.html)
        self.assertIn("state?.view==='settings'?'settings'", self.html)

    def test_settings_catalog_renders_complete_status_and_colored_label_lists(self):
        self.assertIn('function settingsStatusItems(statuses)', self.html)
        self.assertIn('return statuses.map(status=>', self.html)
        self.assertIn('function settingsLabelItems(labels)', self.html)
        self.assertIn('return labels.map(label=>', self.html)
        self.assertIn('class="catalog-items"', self.html)


class WebUiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.board = Board(Path(self.tmp.name) / "board.db")
        self.board.init()
        self.board.configure_board("APP", "App")
        self.actor = self.board.create_actor("web-agent")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.board))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tmp.cleanup()

    def request(self, method, path, *, body=None, token=True, headers=None, parse_json=True):
        merged = {"Content-Type": "application/json"}
        if token:
            merged["Authorization"] = f"Bearer {self.actor['token']}"
        merged.update(headers or {})
        data = json.dumps(body).encode() if body is not None else None
        request = Request(self.url + path, data=data, headers=merged, method=method)
        try:
            with urlopen(request, timeout=3) as response:
                raw = response.read()
                return response.status, (json.loads(raw) if raw and parse_json else raw)
        except HTTPError as error:
            raw = error.read()
            return error.code, (json.loads(raw) if raw and parse_json else raw)

    def test_auth_is_required_for_api_routes(self):
        status, body = self.request("GET", "/api/dashboard", token=False)
        self.assertEqual(status, 401)
        self.assertEqual(body["error"]["code"], "unauthorized")

    def test_health_endpoint_requires_no_auth(self):
        status, body = self.request("GET", "/health", token=False)
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["version"], __version__)

    def test_root_serves_static_index_without_auth(self):
        status, body = self.request("GET", "/", token=False, parse_json=False)
        self.assertEqual(status, 200)
        self.assertIn(b"<html", body.lower())

    def test_dashboard_shape(self):
        status, dashboard = self.request("GET", "/api/dashboard")
        self.assertEqual(status, 200)
        self.assertEqual(set(dashboard), {"board", "issues", "actors", "activity"})
        self.assertEqual(dashboard["board"]["prefix"], "APP")
        self.assertEqual(dashboard["issues"], [])
        self.assertEqual(dashboard["actors"][0]["name"], "web-agent")

    def test_create_issue_via_post(self):
        status, issue = self.request("POST", "/api/issues", body={"title": "Ship it"})
        self.assertEqual(status, 201)
        self.assertEqual(issue["title"], "Ship it")
        self.assertEqual(issue["identifier"], "APP-1")

    def test_patch_status_with_expected_revision(self):
        _, issue = self.request("POST", "/api/issues", body={"title": "Ship it"})
        status, updated = self.request(
            "PATCH",
            f"/api/issues/{issue['identifier']}",
            body={"status": "Todo", "expected_revision": issue["revision"]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["status"], "Todo")
        self.assertEqual(updated["revision"], issue["revision"] + 1)

    def test_stale_revision_returns_409_with_json_body(self):
        _, issue = self.request("POST", "/api/issues", body={"title": "Ship it"})
        status, first = self.request(
            "PATCH",
            f"/api/issues/{issue['identifier']}",
            body={"priority": "high", "expected_revision": issue["revision"]},
        )
        self.assertEqual(status, 200)
        status, stale = self.request(
            "PATCH",
            f"/api/issues/{issue['identifier']}",
            body={"priority": "low", "expected_revision": issue["revision"]},
        )
        self.assertEqual(status, 409)
        self.assertEqual(stale["error"]["code"], "conflict")
        self.assertTrue(stale["error"]["retryable"])
        self.assertIn("error", stale)

    def test_duplicate_label_name_returns_409_json_not_connection_drop(self):
        status, first = self.request("POST", "/api/labels", body={"name": "bug"})
        self.assertEqual(status, 201)
        status, second = self.request("POST", "/api/labels", body={"name": "bug"})
        self.assertEqual(status, 409)
        self.assertIn("error", second)
        self.assertEqual(second["error"]["code"], "conflict")

    def test_oversized_body_returns_413(self):
        # A spoofed Content-Length triggers the same cap deterministically; actually
        # streaming 1 MB races the server's early close and made this test flaky.
        request = Request(
            self.url + "/api/issues",
            data=b"{}",
            headers={
                "Content-Type": "application/json",
                "Content-Length": "2000000",
                "Authorization": f"Bearer {self.actor['token']}",
            },
            method="POST",
        )
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=3)
        self.assertEqual(caught.exception.code, 413)
        body = json.loads(caught.exception.read())
        self.assertIn("error", body)

    def test_create_milestone_then_duplicate_name_conflicts(self):
        status, milestone = self.request("POST", "/api/milestones", body={"name": "Beta"})
        self.assertIn(status, (200, 201))
        self.assertEqual(milestone["name"], "Beta")
        status, dup = self.request("POST", "/api/milestones", body={"name": "Beta"})
        self.assertEqual(status, 409)
        self.assertEqual(dup["error"]["code"], "conflict")
        self.assertIn("error", dup)

    def test_create_label_then_duplicate_name_conflicts(self):
        status, label = self.request("POST", "/api/labels", body={"name": "urgent"})
        self.assertIn(status, (200, 201))
        self.assertEqual(label["name"], "urgent")
        status, dup = self.request("POST", "/api/labels", body={"name": "urgent"})
        self.assertEqual(status, 409)
        self.assertEqual(dup["error"]["code"], "conflict")

    def test_claim_then_release_issue(self):
        _, issue = self.request("POST", "/api/issues", body={"title": "Claim me"})
        status, claimed = self.request(
            "POST",
            f"/api/issues/{issue['identifier']}/claim",
            body={"expected_revision": issue["revision"]},
        )
        self.assertEqual(status, 201)
        self.assertEqual(claimed["assignee"], "web-agent")
        status, released = self.request(
            "POST",
            f"/api/issues/{issue['identifier']}/release",
            body={"expected_revision": claimed["revision"]},
        )
        self.assertEqual(status, 201)
        self.assertIsNone(released["assignee_id"])

    def test_comments_dependencies_and_git_links_via_post(self):
        _, issue = self.request("POST", "/api/issues", body={"title": "Primary"})
        _, blocker = self.request("POST", "/api/issues", body={"title": "Blocker"})
        status, comment = self.request(
            "POST", f"/api/issues/{issue['identifier']}/comments", body={"body": "hello"}
        )
        self.assertEqual(status, 201)
        self.assertEqual(comment["body"], "hello")
        status, with_dependency = self.request(
            "POST",
            f"/api/issues/{issue['identifier']}/dependencies",
            body={"depends_on": blocker["identifier"]},
        )
        self.assertEqual(status, 201)
        self.assertTrue(with_dependency["blocked"])
        status, with_link = self.request(
            "POST",
            f"/api/issues/{issue['identifier']}/git-links",
            body={"ref": "feature/x"},
        )
        self.assertEqual(status, 201)
        self.assertEqual(with_link["ref"], "feature/x")
        self.assertEqual(with_link["kind"], "commit")

    def test_patch_comment_by_author_succeeds(self):
        _, issue = self.request("POST", "/api/issues", body={"title": "Commented"})
        _, comment = self.request(
            "POST", f"/api/issues/{issue['identifier']}/comments", body={"body": "first"}
        )
        status, updated = self.request(
            "PATCH", f"/api/comments/{comment['id']}", body={"body": "edited"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated["body"], "edited")

    def test_patch_comment_by_other_member_is_forbidden(self):
        other = self.board.create_actor("other-agent")
        _, issue = self.request("POST", "/api/issues", body={"title": "Commented"})
        _, comment = self.request(
            "POST", f"/api/issues/{issue['identifier']}/comments", body={"body": "first"}
        )
        status, body = self.request(
            "PATCH",
            f"/api/comments/{comment['id']}",
            body={"body": "hijacked"},
            headers={"Authorization": f"Bearer {other['token']}"},
        )
        self.assertEqual(status, 403)
        self.assertEqual(body["error"]["code"], "unauthorized")
        self.assertIn("error", body)

    def test_patch_label_milestone_and_git_link(self):
        _, label = self.request("POST", "/api/labels", body={"name": "old-name"})
        status, renamed = self.request(
            "PATCH", f"/api/labels/{label['id']}", body={"name": "new-name"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(renamed["name"], "new-name")

        _, milestone = self.request("POST", "/api/milestones", body={"name": "M1"})
        status, renamed_milestone = self.request(
            "PATCH", f"/api/milestones/{milestone['id']}", body={"name": "M2"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(renamed_milestone["name"], "M2")

        _, issue = self.request("POST", "/api/issues", body={"title": "Linked"})
        _, with_link = self.request(
            "POST", f"/api/issues/{issue['identifier']}/git-links", body={"ref": "feature/a"}
        )
        link_id = with_link["id"]
        status, updated_link = self.request(
            "PATCH", f"/api/git-links/{link_id}", body={"ref": "feature/b"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(updated_link["ref"], "feature/b")

    def test_delete_comment_label_milestone_and_git_link(self):
        _, issue = self.request("POST", "/api/issues", body={"title": "To clean up"})
        _, comment = self.request(
            "POST", f"/api/issues/{issue['identifier']}/comments", body={"body": "temp"}
        )
        status, deleted_comment = self.request("DELETE", f"/api/comments/{comment['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(deleted_comment, {"deleted": True, "id": comment["id"]})

        _, label = self.request("POST", "/api/labels", body={"name": "throwaway"})
        status, deleted_label = self.request("DELETE", f"/api/labels/{label['id']}")
        self.assertEqual(status, 200)
        self.assertTrue(deleted_label["deleted"])

        _, milestone = self.request("POST", "/api/milestones", body={"name": "throwaway-m"})
        status, deleted_milestone = self.request("DELETE", f"/api/milestones/{milestone['id']}")
        self.assertEqual(status, 200)
        self.assertTrue(deleted_milestone["deleted"])

        _, with_link = self.request(
            "POST", f"/api/issues/{issue['identifier']}/git-links", body={"ref": "feature/z"}
        )
        link_id = with_link["id"]
        status, deleted_link = self.request("DELETE", f"/api/git-links/{link_id}")
        self.assertEqual(status, 200)
        self.assertTrue(deleted_link["deleted"])

    def test_delete_dependency(self):
        _, issue = self.request("POST", "/api/issues", body={"title": "Depender"})
        _, blocker = self.request("POST", "/api/issues", body={"title": "Depended on"})
        self.request(
            "POST",
            f"/api/issues/{issue['identifier']}/dependencies",
            body={"depends_on": blocker["identifier"]},
        )
        status, freed = self.request(
            "DELETE",
            f"/api/issues/{issue['identifier']}/dependencies",
            body={"depends_on": blocker["identifier"]},
        )
        self.assertEqual(status, 200)
        self.assertFalse(freed["blocked"])

    def test_config_managed_label_edit_is_rejected(self):
        config_path = Path(self.tmp.name) / "project.toml"
        config_path.write_text(default_config("Doctor", "DOC"))
        ConfigService(self.board).apply(load_config(config_path), self.actor["id"])
        status, board_context = self.request("GET", "/api/board")
        managed = next(label for label in board_context["labels"] if label["key"] == "review_required")
        status, body = self.request(
            "PATCH", f"/api/labels/{managed['id']}", body={"name": "renamed"}
        )
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "invalid_request")
        self.assertIn("error", body)

    def test_mcp_batch_with_one_success_and_one_failure(self):
        _, issue = self.request("POST", "/api/issues", body={"title": "Batchable"})
        batch = [
            {
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "get_issue", "arguments": {"issue": issue["identifier"]}},
            },
            {
                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                "params": {"name": "get_issue", "arguments": {"issue": "APP-999"}},
            },
        ]
        status, results = self.request(
            "POST", "/mcp", body=batch,
            headers={"Accept": "application/json, text/event-stream"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(results), 2)
        self.assertNotIn("isError", results[0]["result"])
        self.assertTrue(results[1]["result"]["isError"])
        self.assertEqual(results[1]["result"]["structuredContent"]["error"]["code"], "not_found")

    def test_unknown_route_returns_404_json(self):
        status, body = self.request("GET", "/api/nonexistent")
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "not_found")
        self.assertIn("error", body)

    def test_activity_endpoint_returns_list(self):
        self.request("POST", "/api/issues", body={"title": "For activity"})
        status, activity = self.request("GET", "/api/activity")
        self.assertEqual(status, 200)
        self.assertIsInstance(activity, list)
        self.assertGreater(len(activity), 0)

    def test_activity_endpoint_honors_limit_query_param(self):
        for title in ("One", "Two", "Three"):
            self.request("POST", "/api/issues", body={"title": title})
        status, activity = self.request("GET", "/api/activity?limit=1")
        self.assertEqual(status, 200)
        self.assertEqual(len(activity), 1)

    def test_get_me_and_get_single_issue(self):
        status, me = self.request("GET", "/api/me")
        self.assertEqual(status, 200)
        self.assertEqual(me["name"], "web-agent")
        _, issue = self.request("POST", "/api/issues", body={"title": "Fetch me"})
        status, fetched = self.request("GET", f"/api/issues/{issue['identifier']}")
        self.assertEqual(status, 200)
        self.assertEqual(fetched["identifier"], issue["identifier"])

    def test_mcp_get_is_method_not_allowed(self):
        request = Request(self.url + "/mcp", method="GET",
                          headers={"Authorization": f"Bearer {self.actor['token']}"})
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=3)
        self.assertEqual(caught.exception.code, 405)
        self.assertEqual(caught.exception.headers.get("Allow"), "POST")

    def test_mcp_rejects_wrong_content_type(self):
        request = Request(
            self.url + "/mcp",
            data=b"{}",
            headers={
                "Authorization": f"Bearer {self.actor['token']}",
                "Content-Type": "text/plain",
                "Accept": "application/json, text/event-stream",
            },
            method="POST",
        )
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=3)
        self.assertEqual(caught.exception.code, 415)
        body = json.loads(caught.exception.read())
        self.assertEqual(body["error"]["code"], "invalid_request")

    def test_mcp_rejects_missing_accept_header(self):
        request = Request(
            self.url + "/mcp",
            data=b"{}",
            headers={
                "Authorization": f"Bearer {self.actor['token']}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        with self.assertRaises(HTTPError) as caught:
            urlopen(request, timeout=3)
        self.assertEqual(caught.exception.code, 406)
        body = json.loads(caught.exception.read())
        self.assertEqual(body["error"]["code"], "invalid_request")

    def test_post_unknown_issue_action_and_unknown_route_return_404(self):
        _, issue = self.request("POST", "/api/issues", body={"title": "Actionless"})
        status, body = self.request(
            "POST", f"/api/issues/{issue['identifier']}/not-a-real-action", body={}
        )
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "not_found")
        status, body = self.request("POST", "/api/not-a-real-route", body={})
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "not_found")

    def test_patch_without_token_and_unknown_route_return_expected_errors(self):
        status, body = self.request("PATCH", "/api/labels/1", body={"name": "x"}, token=False)
        self.assertEqual(status, 401)
        self.assertEqual(body["error"]["code"], "unauthorized")
        status, body = self.request("PATCH", "/api/issues", body={})
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "not_found")
        status, body = self.request("PATCH", "/api/not-a-real-entity/1", body={"name": "x"})
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "not_found")

    def test_delete_without_token_and_unknown_route_return_expected_errors(self):
        status, body = self.request("DELETE", "/api/labels/1", token=False)
        self.assertEqual(status, 401)
        self.assertEqual(body["error"]["code"], "unauthorized")
        status, body = self.request("DELETE", "/api/not-a-real-entity/1")
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "not_found")
        status, body = self.request("DELETE", "/api/comments/999999")
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "not_found")

    def test_patch_and_delete_oversized_body_returns_413(self):
        # A declared Content-Length beyond the cap is rejected before the body is read,
        # so only a tiny payload actually needs to cross the wire here.
        for method in ("PATCH", "DELETE"):
            request = Request(
                self.url + "/api/comments/1",
                data=b"{}",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.actor['token']}",
                    "Content-Length": "2000000",
                },
                method=method,
            )
            with self.assertRaises(HTTPError) as caught:
                urlopen(request, timeout=3)
            self.assertEqual(caught.exception.code, 413)
            body = json.loads(caught.exception.read())
            self.assertIn("error", body)


if __name__ == "__main__":
    unittest.main()
