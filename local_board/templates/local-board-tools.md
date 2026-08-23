# Tool selection

- Read: `whoami`, `get_board_context`, `list_issues`, `get_issue`, `list_activity`.
- Write (member): `create_issue`, `update_issue`, `claim_issue`, `release_issue`, `add_comment`, `update_comment`, `add_dependency`, `remove_dependency`, `add_git_link`, `create_milestone`, `create_label`.
- Admin only: update/delete label and milestone, `delete_comment`, update/delete git link, `create_actor`, `rotate_actor_token`, `set_actor_role`.

`update_issue` accepts any field, including `status` and `labels`, and requires `expected_revision` from the latest read or mutation — a stale value is rejected as a conflict, not silently overwritten. `claim_issue` and `release_issue` also require `expected_revision`.

Use stable references — the issue identifier (`APP-12`), actor name, label key, milestone key — instead of internal database IDs wherever a tool accepts one.
