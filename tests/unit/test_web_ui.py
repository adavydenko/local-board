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
        cls.css_dir = Path(__file__).parents[2] / "local_board" / "static" / "css"
        cls.css = "\n".join(path.read_text() for path in sorted(cls.css_dir.glob("*.css")))
        cls.js_dir = Path(__file__).parents[2] / "local_board" / "static" / "js"
        cls.js = "\n".join(path.read_text() for path in sorted(cls.js_dir.rglob("*.js")))

    def test_all_css_colors_are_tokenized(self):
        """Every color in CSS must reference a :root token; new colors get new tokens.

        This is a ratchet: outside tokens.css's :root block, no hex or
        rgb()/rgba() literal may appear in any stylesheet under static/css.
        It keeps the palette themeable from one place instead of drifting
        back into scattered literals.
        """
        import re

        for css_path in sorted(self.css_dir.glob("*.css")):
            css = css_path.read_text()
            if css_path.name == "tokens.css":
                continue
            literals = re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)", css)
            self.assertEqual(
                literals, [], f"color literals outside tokens.css in {css_path.name}: {literals}"
            )

    def test_issue_workspace_defaults_to_a_grouped_list_with_optional_board_view(self):
        self.assertIn('id="issueList"', self.html)
        self.assertIn('data-layout="list"', self.html)
        self.assertIn('data-layout="board"', self.html)
        self.assertIn("currentLayout: 'list',", self.js)

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
        self.assertIn('assignee_id:assignee?+assignee:null', self.js)

    def test_new_issue_in_started_status_defaults_to_current_actor(self):
        self.assertIn("export function defaultNewIssueAssignee(status)", self.js)
        self.assertIn("statusCategory(status)==='started'?store.identity?.id:null", self.js)
        self.assertIn(
            "issueAssignee.innerHTML=actorOptions(defaultNewIssueAssignee(status))",
            self.js,
        )

    def test_initial_restore_does_not_steal_focus_from_the_skip_link(self):
        self.assertIn("{updateHistory:false,focusContent:false}", self.js)

    def test_active_primary_navigation_exposes_the_current_page(self):
        self.assertIn("setAttribute('aria-current',active?'page':'false')", self.js)

    def test_external_git_links_reject_unsafe_url_schemes(self):
        self.assertIn("export function safeExternalUrl(value)", self.js)
        self.assertIn("['http:','https:'].includes(url.protocol)", self.js)

    def test_history_restores_views_and_direct_issue_links_stay_in_the_app(self):
        self.assertIn("history.state?.fromApp", self.js)
        self.assertIn("state?.view==='activity'", self.js)
        self.assertIn("history.replaceState({view:'issues'}", self.js)

    def test_claim_conflicts_reload_current_issue_state(self):
        self.assertIn("export async function mutateClaim(action,message)", self.js)
        self.assertIn("await refreshDetail()", self.js)

    def test_mobile_keeps_the_primary_filters_available(self):
        self.assertIn("#milestoneFilter{grid-column:1}", self.css)
        self.assertIn("#assigneeFilter{grid-column:2}", self.css)
        self.assertNotIn(".toolbar select{display:none}", self.css)

    def test_viewer_role_gets_a_read_only_issue_workspace(self):
        self.assertIn("export function canWrite(){return store.identity?.role!=='viewer'}", self.js)
        self.assertIn("if(!canWrite())return readOnlyProperties(issue)", self.js)

    def test_settings_overview_exposes_repository_managed_configuration(self):
        self.assertIn('id="settingsView"', self.html)
        self.assertIn('Managed by <code>.local-board/project.toml</code>', self.html)
        self.assertIn('id="configPreview"', self.html)
        self.assertIn('export function projectToml()', self.js)

    def test_settings_is_a_first_class_navigation_and_history_view(self):
        self.assertIn('data-view="settings"', self.html)
        self.assertIn("settingsView.classList.toggle('hidden',view!=='settings')", self.js)
        self.assertIn("state?.view==='settings'?'settings'", self.js)

    def test_settings_catalog_renders_complete_status_and_colored_label_lists(self):
        self.assertIn('export function settingsStatusItems(statuses)', self.js)
        self.assertIn('return statuses.map(status=>', self.js)
        self.assertIn('export function settingsLabelItems(labels)', self.js)
        self.assertIn('return labels.map(label=>', self.js)
        self.assertIn('class="catalog-items"', self.js)

    def test_settings_offers_a_read_first_milestone_manager(self):
        self.assertIn('id="settingsMilestones"', self.html)
        self.assertIn('data-action="create-milestone"', self.js)
        self.assertIn('export function milestoneProgress(milestone)', self.js)
        self.assertIn('/api/milestones', self.js)
        self.assertIn('Configuration-managed milestones are edited in <code>project.toml</code>.', self.html)

    def test_settings_gives_milestones_a_dedicated_tab(self):
        self.assertIn('role="tablist" aria-label="Settings sections"', self.html)
        self.assertIn('data-settings-tab="overview"', self.html)
        self.assertIn('data-settings-tab="milestones"', self.html)
        self.assertIn('id="settingsOverviewPanel"', self.html)
        self.assertIn('id="settingsMilestonesPanel"', self.html)
        self.assertIn('export function setSettingsTab(tab)', self.js)

    def test_settings_gives_labels_a_dedicated_management_tab_and_issue_side_creation(self):
        self.assertIn('data-settings-tab="labels"', self.html)
        self.assertIn('id="settingsLabelsPanel"', self.html)
        self.assertIn('id="settingsLabels"', self.html)
        self.assertIn('data-form="create-label"', self.js)
        self.assertIn('data-action="start-create-issue-label"', self.js)
        self.assertIn("api('/api/labels',{method:'POST'", self.js)

    def test_issue_label_creation_stays_in_an_anchored_visible_picker(self):
        self.assertIn('.label-options{position:absolute;', self.css)
        self.assertIn("$$('details[data-property-picker=\"labels\"]').find(item=>item.getClientRects().length)", self.js)
        self.assertIn("picker.querySelector('input[name=\"name\"]')?.focus()", self.js)

    def test_status_indicators_follow_categories_across_issue_views(self):
        for category in ("backlog", "unstarted", "started", "completed", "canceled"):
            self.assertIn(f".status-indicator.{category}", self.css)
        self.assertIn("export function issueRow(issue,status)", self.js)
        self.assertIn("items.map(issue=>issueRow(issue,status))", self.js)
        self.assertIn("export function boardCard(issue,status)", self.js)
        self.assertIn("items.map(issue=>boardCard(issue,status))", self.js)

    def test_active_issue_filters_hide_empty_status_groups(self):
        self.assertIn('export function hasActiveIssueFilters()', self.js)
        self.assertIn('const visibleStatuses=hasActiveIssueFilters()?statuses.filter', self.js)
        self.assertIn('No issues match the active filters.', self.js)

    def test_issue_detail_keeps_secondary_context_in_a_compact_rail(self):
        self.assertIn('export function settingsStatusFlow(statuses)', self.js)
        self.assertIn('class="status-flow"', self.js)
        self.assertIn('class="issue-sidebar"', self.html)
        self.assertIn('<h2 class="sidebar-section-heading">Blocking</h2>', self.js)
        self.assertIn('<h2 class="sidebar-section-heading">Git links</h2>', self.js)
        self.assertIn('class="label-editor"', self.js)
        self.assertIn('Add label', self.js)
        self.assertNotIn('id="pageTitle"', self.html)

    def test_issue_workspace_offers_on_demand_narrative_and_author_comment_editing(self):
        self.assertIn('data-action="edit-issue"', self.js)
        self.assertIn('data-form="edit-issue"', self.js)
        self.assertIn('data-action="edit-comment"', self.js)
        self.assertIn('data-form="edit-comment"', self.js)
        self.assertIn('class="inline-edit comment-edit"', self.js)
        self.assertIn("comment.author_id===store.identity?.id||store.identity?.role==='admin'", self.js)
        self.assertIn("comment.updated_at!==comment.created_at", self.js)

    def test_inline_edit_actions_do_not_reserve_issue_description_width(self):
        self.assertNotIn('.description-wrap>.markdown{padding-right', self.css)
        self.assertIn('.description-wrap .inline-edit{position:absolute;right:-36px', self.css)


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

    def _served_js(self):
        """Concatenated bytes of every ES module the client actually ships."""
        js_dir = Path(__file__).parents[2] / "local_board" / "static" / "js"
        relative_paths = sorted(path.relative_to(js_dir).as_posix() for path in js_dir.rglob("*.js"))
        combined = b""
        for relative_path in relative_paths:
            status, body = self.request(
                "GET", f"/static/js/{relative_path}", token=False, parse_json=False
            )
            self.assertEqual(status, 200)
            combined += body
        return combined

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

    def test_static_assets_are_served_with_exact_content_types(self):
        """Browsers refuse ES modules with a wrong Content-Type, so pin each one."""
        static_root = Path(__file__).parents[2] / "local_board" / "static"
        expected = {"css": "text/css; charset=utf-8",
                    "js": "text/javascript; charset=utf-8",
                    "svg": "image/svg+xml"}
        for extension, content_type in expected.items():
            probe = static_root / f"probe-test.{extension}"
            probe.write_text("/* probe */", encoding="utf-8")
            self.addCleanup(probe.unlink)
            request = Request(f"{self.url}/static/probe-test.{extension}")
            with urlopen(request, timeout=3) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers["Content-Type"], content_type)
                self.assertEqual(response.headers["Cache-Control"], "no-cache")

    def test_static_route_rejects_traversal_hidden_and_unknown_types(self):
        for path in ("/static/../pyproject.toml",
                     "/static/%2e%2e/db.py",
                     "/static/.hidden.css",
                     "/static/index.html",
                     "/static/missing.css",
                     "/static/"):
            status, body = self.request("GET", path, token=False)
            self.assertEqual(status, 404, path)
            self.assertEqual(body["error"]["code"], "not_found", path)

    def test_root_serves_static_index_without_auth(self):
        status, body = self.request("GET", "/", token=False, parse_json=False)
        self.assertEqual(status, 200)
        self.assertIn(b"<html", body.lower())

    def test_root_serves_a_multiline_comment_composer(self):
        body = self._served_js()
        self.assertIn(b'<textarea class="control comment-input"', body)
        self.assertIn(b'data-action="cancel-comment"', body)
        self.assertIn(b"Markdown supported", body)

    def test_root_serves_on_demand_issue_property_pickers(self):
        body = self._served_js()
        self.assertIn(b'class="property-picker"', body)
        self.assertIn(b'data-action="property-trigger"', body)
        self.assertIn(b'data-action="set-property"', body)
        self.assertNotIn(b'<select data-field="status">', body)

    def test_root_serves_dismissible_property_pickers_and_actionable_empty_states(self):
        body = self._served_js()
        self.assertIn(b"function dismissPropertyPickers", body)
        self.assertIn(b"function focusPropertyPicker", body)
        self.assertIn(b"event.key!=='Escape'", body)
        self.assertIn("No blockers — this issue can move forward.".encode(), body)
        self.assertIn(b"No linked Git work yet", body)
        self.assertIn("No comments yet — add context".encode(), body)

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

    def test_dashboard_exposes_each_milestones_management_source(self):
        _, milestone = self.request("POST", "/api/milestones", body={"name": "Board milestone"})
        status, dashboard = self.request("GET", "/api/dashboard")
        self.assertEqual(status, 200)
        listed = next(item for item in dashboard["board"]["milestones"] if item["id"] == milestone["id"])
        self.assertEqual(listed["managed_by"], "manual")

    def test_dashboard_exposes_each_labels_management_source(self):
        _, label = self.request("POST", "/api/labels", body={"name": "Board label"})
        status, dashboard = self.request("GET", "/api/dashboard")
        self.assertEqual(status, 200)
        listed = next(item for item in dashboard["board"]["labels"] if item["id"] == label["id"])
        self.assertEqual(listed["managed_by"], "manual")

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
