import importlib.util
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from local_board.db import Board
from local_board.web import make_handler


PLAYWRIGHT_AVAILABLE = importlib.util.find_spec("playwright") is not None
BROWSER_AVAILABLE = bool(
    list((Path.home() / ".cache" / "ms-playwright").glob("chromium-*/chrome-linux/chrome"))
    or list((Path.home() / ".cache" / "ms-playwright").glob("chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell"))
)


@unittest.skipUnless(PLAYWRIGHT_AVAILABLE and BROWSER_AVAILABLE, "install the browser-test extra and Chromium")
class BrowserE2ETest(unittest.TestCase):
    def test_login_load_columns_and_create_issue(self):
        from playwright.sync_api import sync_playwright

        with tempfile.TemporaryDirectory() as tmp:
            board = Board(Path(tmp) / "board.db")
            board.init()
            actor = board.create_actor("browser-agent")
            board.create_project(actor["id"], "WEB", "Browser project")
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(board))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch()
                    page = browser.new_page()
                    page.goto(f"http://127.0.0.1:{server.server_port}/")
                    page.locator("#tokenInput").fill(actor["token"])
                    page.locator("#loginForm button").click()
                    page.locator("#login.hidden").wait_for()
                    self.assertGreater(page.locator("#board .column").count(), 0)
                    page.locator("#newIssueBtn").click()
                    page.locator("#issueTitle").fill("Created in Chromium")
                    page.locator("#issueForm button[type=submit]").click()
                    page.get_by_text("Created in Chromium", exact=True).wait_for()
                    self.assertEqual(board.list_issues()[0]["title"], "Created in Chromium")
                    browser.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
