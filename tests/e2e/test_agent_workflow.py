import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

from local_board.db import Board
from local_board.web import make_handler


class TwoAgentWorkflowE2ETest(unittest.TestCase):
    def test_two_tokens_preserve_authorship_across_full_workflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            board = Board(Path(tmp) / "board.db")
            board.init()
            alice = board.create_actor("alice")
            bob = board.create_actor("bob")
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(board))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f"http://127.0.0.1:{server.server_port}"

            def post(token, path, body):
                request = Request(
                    url + path,
                    data=json.dumps(body).encode(),
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                )
                with urlopen(request, timeout=3) as response:
                    return json.load(response)

            try:
                project = post(alice["token"], "/api/projects", {"key": "TEAM", "name": "Team"})
                issue = post(alice["token"], "/api/issues", {"project_id": project["id"], "title": "Collaborate"})
                post(bob["token"], f"/api/issues/{issue['identifier']}/comments", {"body": "I will handle this"})
                claimed = post(bob["token"], f"/api/issues/{issue['identifier']}/claim", {"expected_revision": issue["revision"]})
                moved = post(bob["token"], f"/api/issues/{issue['identifier']}/transition", {"status": "todo", "expected_revision": claimed["revision"]})
                post(bob["token"], f"/api/issues/{issue['identifier']}/git-links", {"link_kind": "branch", "ref": "TEAM-1-work"})
                self.assertEqual(moved["status"], "todo")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

            context = board.get_issue_context(issue["id"])
            self.assertEqual(context["comments"][0]["author"], "bob")
            self.assertEqual(context["assignee"], "bob")
            authored = {(event["action"], event["actor"]) for event in context["activity"]}
            self.assertIn(("created", "alice"), authored)
            for action in ("comment_added", "claimed", "transitioned", "git_link_added"):
                self.assertIn((action, "bob"), authored)


if __name__ == "__main__":
    unittest.main()
