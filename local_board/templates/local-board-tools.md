# Tool selection

Use `tools/list` as the authoritative schema. Select tools by intent:

- Identity and discovery: `whoami`, `list_actors`, `list_projects`, `get_project_context`, `list_workflows`, `get_workflow`, `list_labels`, `get_label`, `list_milestones`, `get_milestone`.
- Issue discovery: `list_issues`, `get_issue_context`, `get_available_transitions`.
- Issue ownership and state: `create_issue`, `update_issue`, `claim_issue`, `release_issue`, `transition_issue`.
- Discussion and execution: `add_comment`, `update_comment`, `delete_comment`, `add_checklist_item`, `update_checklist_item`, `complete_checklist_item`, `delete_checklist_item`.
- Relationships and evidence: `add_dependency`, `remove_dependency`, `add_attachment`, `delete_attachment`, `add_git_link`, `delete_git_link`, `add_label`, `remove_label`.
- Administration: `create_project`, `create_milestone`, `create_label`, `set_workflow`, `list_activity`, `list_releases`, `create_release`, `transition_release`.
- Access control: `create_actor`, `rotate_actor_token`, and `set_actor_role` are admin-only; viewers may call discovery and read tools only. Capture newly returned tokens once and transfer them only through an untracked secret channel. Activity is immutable.

Prefer stable project keys, actor names, label keys, milestone keys, and issue identifiers over numeric database IDs. Every issue mutation that accepts `expected_revision` must use the value returned by the latest read or mutation.
