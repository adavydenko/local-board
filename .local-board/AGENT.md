# Local Board agent policy

This repository uses Local Board as its issue board.

Before changing code, use the configured Local Board MCP server to identify yourself, find or create the issue, read it, and claim it. Keep comments, labels, dependencies, and status current. Use stable issue identifiers (`APP-12`) in branches and handoffs. Never access `.local-board/state/board.db` directly and never commit or disclose bearer tokens.

Convention: an issue labeled `review_required` is moved to an In Review status with a comment on what to check, instead of straight to Done — adapt this to the project's own rules.
