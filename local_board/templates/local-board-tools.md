# Tool signatures

`issue` = `APP-12` style identifier; `milestone`/`label`/`actor` accept key, name, or id. `expected_revision` must come from your latest read or mutation response.

Required params are bare; optional params are marked `?`. Trailing marker: `rev+` advances the issue's revision, `rev=` does not.

READ:
- `whoami()`
- `get_board_context()`
- `list_issues(status?, milestone?, assignee?, label?, parent?, query?)`
- `get_issue(issue)`
- `list_activity(entity_type?, entity_id?, limit?)`

WRITE (member):
- `create_issue(title, description?, priority?, status?, milestone?, parent?, assignee?, labels?)` rev+ (new issue)
- `update_issue(issue, expected_revision, title?, description?, priority?, status?, assignee?, milestone?, parent?, labels?, position?, return_full_issue?)` rev+
- `claim_issue(issue, expected_revision, lease_seconds?, status?, return_full_issue?)` rev+
- `release_issue(issue, expected_revision, return_full_issue?)` rev+
- `add_comment(issue, body)` rev= (returns comment plus `issue_revision`)
- `update_comment(comment_id, body)` rev=
- `add_dependency(issue, depends_on, return_full_issue?)` rev=
- `remove_dependency(issue, depends_on, return_full_issue?)` rev=
- `add_git_link(issue, ref, kind?, url?, return_full_issue?)` rev=
- `create_milestone(name, key?, description?, due_at?)`
- `create_label(name, key?, color?)`

ADMIN-ONLY:
- `update_label(label, name?, color?)`
- `delete_label(label)`
- `update_milestone(milestone, name?, description?, due_at?)`
- `delete_milestone(milestone)`
- `delete_comment(comment_id)`
- `update_git_link(link_id, kind?, ref?, url?)`
- `delete_git_link(link_id)`
- `create_actor(name, kind?, role?)`
- `rotate_actor_token(actor)`
- `set_actor_role(actor, role)`
