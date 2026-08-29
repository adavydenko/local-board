"""Boot a disposable Local Board server for the Playwright UI suite.

Runs a seeded board on the requested port and writes the admin actor's
token to .fixture-token and a second, read-only viewer actor's token to
.fixture-viewer-token (both next to this file), then serves until
terminated. The seed mirrors the scenarios the specs exercise: an assigned
In Review issue with labels, a milestone, a blocking dependency, a comment,
and both a safe and an unsafe (javascript:) Git link.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from local_board.db import Board
from local_board.web import make_handler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=43117)
    args = parser.parse_args()

    tmp = tempfile.TemporaryDirectory()
    board = Board(Path(tmp.name) / "board.db")
    board.init()
    board.configure_board("E2E", "E2E board")
    actor = board.create_actor("alex", kind="human", role="admin")
    actor_id = actor["id"]
    viewer = board.create_actor("vera", kind="human", role="viewer")

    bug = board.create_label(actor_id, "Bug", color="#d14b5a")
    # Two attached labels push the "Add label" trigger toward the viewport edge,
    # reproducing the audited picker-overflow geometry on a 1280px window.
    review = board.create_label(actor_id, "Review required", color="#b86b18")
    board.create_label(actor_id, "Feature", color="#5e6ad2")
    milestone = board.create_milestone(actor_id, "M1 — Web app MVP")

    blocker = board.create_issue(
        actor_id, "Improve issue property pickers",
        status="In Progress", priority="high", assignee_id=actor_id,
    )
    main_issue = board.create_issue(
        actor_id, "Verify label creation flow",
        "A newly created label should remain in the visible picker.",
        status="In Review", priority="urgent", assignee_id=actor_id,
        milestone_id=milestone["id"], labels=[bug["id"], review["id"]],
    )
    board.create_issue(actor_id, "Keyboard navigation", status="Todo", priority="medium")
    board.add_dependency(actor_id, main_issue["id"], blocker["id"])
    board.add_comment(actor_id, main_issue["id"], "The first interaction should keep the picker open.")
    board.add_git_link(actor_id, main_issue["id"], "safe-pr-1", kind="pr", url="https://example.com/pr/1")
    # safeExternalUrl() must refuse to render this as a clickable link — the
    # server stores whatever URL an actor sends, so the client is the only
    # thing standing between a malicious ref and script execution.
    board.add_git_link(actor_id, main_issue["id"], "unsafe-js-link", kind="commit", url="javascript:alert(1)")

    (Path(__file__).parent / ".fixture-token").write_text(actor["token"])
    (Path(__file__).parent / ".fixture-viewer-token").write_text(viewer["token"])

    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(board))
    print(f"serving on http://127.0.0.1:{args.port} as {actor['name']}", flush=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        thread.join()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
