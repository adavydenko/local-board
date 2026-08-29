import json
import tempfile
import threading
import unittest
from html.parser import HTMLParser
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from local_board import __version__
from local_board.config import ConfigService, default_config, load_config
from local_board.db import Board
from local_board.web import make_handler


_VOID_ELEMENTS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}


class _Node:
    """One element in a parsed HTML tree: a tag, its attributes, and children.

    Deliberately minimal — just enough surface (`find`, `find_all`, `text`,
    `classes`) for the structural shell contracts below. Not a general HTML
    tree; e.g. there's no CSS-selector combinator support, just tag/attr
    matching over descendants.
    """

    def __init__(self, tag, attrs, parent=None):
        self.tag = tag
        self.attrs = dict(attrs)
        self.parent = parent
        self.children: list["_Node"] = []
        self._text_parts: list[str] = []

    def classes(self):
        return set((self.attrs.get("class") or "").split())

    def text(self):
        parts = list(self._text_parts)
        for child in self.children:
            parts.append(child.text())
        return "".join(parts)

    def matches(self, tag=None, attrs=None, **kwargs):
        if tag is not None and self.tag != tag:
            return False
        wanted = dict(attrs or {})
        wanted.update(kwargs)
        return all(self.attrs.get(key) == value for key, value in wanted.items())

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()

    def find(self, tag=None, attrs=None, **kwargs):
        for node in self.walk():
            if node is not self and node.matches(tag, attrs, **kwargs):
                return node
        return None

    def find_all(self, tag=None, attrs=None, **kwargs):
        return [node for node in self.walk() if node is not self and node.matches(tag, attrs, **kwargs)]


class MarkupTree(_Node, HTMLParser):
    """Parses one HTML document into a `_Node` tree via stdlib html.parser.

    Lets the tests below assert on the *structure* of the static shell
    (elements, attributes, nesting) instead of matching substrings in the
    raw source — substring checks break on harmless reformatting and can't
    distinguish a real attribute from text that merely looks like one.
    """

    def __init__(self, html):
        _Node.__init__(self, "[document]", {})
        HTMLParser.__init__(self, convert_charrefs=True)
        self._stack = [self]
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, attrs, parent=self._stack[-1])
        self._stack[-1].children.append(node)
        if tag not in _VOID_ELEMENTS:
            self._stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self._stack[-1].children.append(_Node(tag, attrs, parent=self._stack[-1]))

    def handle_endtag(self, tag):
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                return

    def handle_data(self, data):
        self._stack[-1]._text_parts.append(data)


class WebUiShellTest(unittest.TestCase):
    """Structural contracts for the static HTML shell (local_board/static/index.html).

    Formerly WebUiMarkupTest: that class asserted on raw substrings of the
    concatenated HTML/CSS/JS sources, including JS behavior that a markup
    parser can't see at all. Behavioral contracts (what happens when you
    click things, navigate, or load data) now live in
    tests/e2e_ui/contracts.spec.js as real browser interactions instead of
    string matches on the JS that implements them — see that file's header
    and the migration mapping in the commit message for where each old
    test's contract landed. What's left here are checks about the shape of
    the shell markup itself, done structurally via `MarkupTree`, plus a
    small, clearly-marked section of ratchets for which a substring check
    is still the right tool.
    """

    @classmethod
    def setUpClass(cls):
        static_root = Path(__file__).parents[2] / "local_board" / "static"
        cls.html = (static_root / "index.html").read_text()
        cls.markup = MarkupTree(cls.html)
        cls.css_dir = static_root / "css"
        cls.css = "\n".join(path.read_text() for path in sorted(cls.css_dir.glob("*.css")))

    def test_head_declares_the_six_stylesheets_in_load_order(self):
        stylesheets = [link.attrs.get("href") for link in self.markup.find_all("link", attrs={"rel": "stylesheet"})]
        self.assertEqual(stylesheets, [
            "/static/css/tokens.css", "/static/css/base.css", "/static/css/shell.css",
            "/static/css/issues.css", "/static/css/issue-detail.css", "/static/css/settings.css",
        ])

    def test_head_uses_a_data_uri_favicon_without_an_authenticated_request(self):
        icon = self.markup.find("link", attrs={"rel": "icon"})
        self.assertIsNotNone(icon)
        self.assertEqual(icon.attrs.get("href"), "data:,")

    def test_client_boots_from_a_single_module_script(self):
        scripts = self.markup.find_all("script")
        self.assertEqual(len(scripts), 1)
        self.assertEqual(scripts[0].attrs.get("type"), "module")
        self.assertEqual(scripts[0].attrs.get("src"), "/static/js/main.js")

    def test_skip_link_and_primary_navigation_are_labeled_for_assistive_tech(self):
        skip = self.markup.find("a", attrs={"class": "skip-link"})
        self.assertIsNotNone(skip)
        self.assertEqual(skip.attrs.get("href"), "#mainContent")
        main = self.markup.find(attrs={"id": "mainContent"})
        self.assertIsNotNone(main)
        self.assertEqual(main.attrs.get("tabindex"), "-1")
        nav = self.markup.find("nav", attrs={"aria-label": "Primary navigation"})
        self.assertIsNotNone(nav)
        self.assertEqual(
            {button.attrs.get("data-view") for button in nav.find_all("button")},
            {"issues", "activity", "settings"},
        )

    def test_status_toast_is_an_accessible_live_region(self):
        toast = self.markup.find(attrs={"id": "toast"})
        self.assertIsNotNone(toast)
        self.assertEqual(toast.attrs.get("role"), "status")
        self.assertEqual(toast.attrs.get("aria-live"), "polite")

    def test_issue_workspace_offers_list_and_board_containers_and_a_layout_toggle(self):
        self.assertIsNotNone(self.markup.find(attrs={"id": "issueList"}))
        self.assertIsNotNone(self.markup.find(attrs={"id": "issueBoard"}))
        toggle = self.markup.find(attrs={"aria-label": "Issue layout"})
        self.assertIsNotNone(toggle)
        self.assertEqual(
            {button.attrs.get("data-layout") for button in toggle.find_all("button")},
            {"list", "board"},
        )

    def test_issue_detail_is_a_page_section_not_a_dialog(self):
        detail = self.markup.find(attrs={"id": "issueView"})
        self.assertIsNotNone(detail)
        self.assertEqual(detail.tag, "section")
        self.assertIn("hidden", detail.classes())
        self.assertIsNotNone(self.markup.find("button", attrs={"data-action": "back-to-issues"}))
        self.assertIsNone(self.markup.find("dialog", attrs={"id": "detailDialog"}))
        self.assertEqual([dialog.attrs.get("id") for dialog in self.markup.find_all("dialog")], ["issueDialog"])

    def test_no_checklist_style_controls_in_the_shell(self):
        self.assertEqual(self.markup.find_all("input", attrs={"type": "checkbox"}), [])
        offending = [
            node.tag for node in self.markup.walk()
            if any(token == "check" or token.startswith("checklist") for token in node.classes())
        ]
        self.assertEqual(offending, [])

    def test_new_issue_dialog_offers_priority_milestone_and_assignee_fields(self):
        dialog = self.markup.find("dialog", attrs={"id": "issueDialog"})
        self.assertIsNotNone(dialog)
        for field_id in ("issuePriority", "issueMilestone", "issueAssignee"):
            self.assertIsNotNone(dialog.find(attrs={"id": field_id}), field_id)

    def test_settings_view_exposes_a_tablist_with_three_panels(self):
        settings = self.markup.find(attrs={"id": "settingsView"})
        self.assertIsNotNone(settings)
        self.assertIsNotNone(self.markup.find("button", attrs={"data-view": "settings"}))
        tablist = settings.find(attrs={"role": "tablist"})
        self.assertIsNotNone(tablist)
        self.assertEqual(tablist.attrs.get("aria-label"), "Settings sections")
        tabs = {tab.attrs.get("data-settings-tab"): tab for tab in tablist.find_all("button", attrs={"role": "tab"})}
        self.assertEqual(set(tabs), {"overview", "milestones", "labels"})
        panel_ids = {
            "overview": "settingsOverviewPanel",
            "milestones": "settingsMilestonesPanel",
            "labels": "settingsLabelsPanel",
        }
        for name, tab in tabs.items():
            panel_id = panel_ids[name]
            self.assertEqual(tab.attrs.get("aria-controls"), panel_id)
            self.assertIsNotNone(settings.find(attrs={"id": panel_id}), panel_id)

    def test_settings_overview_declares_its_managed_by_note_and_config_preview(self):
        self.assertIsNotNone(self.markup.find(attrs={"id": "configPreview"}))
        note = self.markup.find(attrs={"id": "settingsManagedNote"})
        self.assertIsNotNone(note)
        self.assertIn("Managed by", note.text())
        code = note.find("code")
        self.assertIsNotNone(code)
        self.assertEqual(code.text(), ".local-board/project.toml")

    def test_settings_milestones_panel_explains_config_managed_milestones(self):
        panel = self.markup.find(attrs={"id": "settingsMilestonesPanel"})
        self.assertIsNotNone(panel)
        self.assertIsNotNone(self.markup.find(attrs={"id": "settingsMilestones"}))
        self.assertIn("project.toml", panel.text())

    def test_issue_sidebar_landmark_exists_and_legacy_page_title_is_gone(self):
        sidebar = self.markup.find(attrs={"id": "issueSidebar"})
        self.assertIsNotNone(sidebar)
        self.assertEqual(sidebar.attrs.get("aria-label"), "Issue details")
        self.assertIsNone(self.markup.find(attrs={"id": "pageTitle"}))

    # --- String ratchets --------------------------------------------------
    # Everything below intentionally checks raw source text instead of
    # parsed structure or rendered behavior. These are invariants about the
    # *implementation* (no stray color literals, every status category has
    # CSS backing) rather than product contracts, so a substring check is
    # the right tool here and a structural or Playwright test would cost
    # more for no extra confidence.

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

    def test_status_indicator_css_classes_cover_every_status_category(self):
        """Ratchet, kept as a substring check on purpose.

        The five status categories (backlog/unstarted/started/completed/
        canceled) are a closed set baked into the database's CHECK
        constraint (see db.py). Every category needs a `.status-indicator.*`
        rule or issues in that category render an unstyled dot. There's no
        practical way to exercise all five categories through the running
        app in one Playwright pass — the fixture board only ever configures
        a handful of statuses at once — so this stays a direct check
        against the stylesheet text rather than a structural or e2e one.
        """
        for category in ("backlog", "unstarted", "started", "completed", "canceled"):
            self.assertIn(f".status-indicator.{category}", self.css)


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
