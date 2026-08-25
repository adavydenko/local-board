# Local Board agent policy

This repository uses Local Board as its issue board. This file is installed by `local-board init` and reaches agents through the root `AGENTS.md` discovery bridge; it states the repository's board policy, while the `local-board` skill under `.agents/skills/` teaches the mechanics.

Before changing code, use the configured Local Board MCP server to identify yourself, find or create the issue, read it, and claim it. Keep comments, labels, dependencies, and status current. Use stable issue identifiers (`APP-12`) in branches and handoffs. Never access `.local-board/state/board.db` directly and never commit or disclose bearer tokens. The board is authoritative for coordination — who claimed what, what was decided, task statements and outcomes; git remains authoritative for code state, and board status is updated by agents so it can lag behind commits.

Convention: an issue labeled `review_required` is moved to an In Review status with a comment on what to check, instead of straight to Done — adapt this to the project's own rules.

Any script or command you cite as verification in a comment must be committed to the repository first; an uncommitted verification run is an unreproducible screenshot for the next agent.
