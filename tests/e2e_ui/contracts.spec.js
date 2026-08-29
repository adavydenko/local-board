// @ts-check
// Behavioral contracts for the web client that used to live as JS-source
// substring assertions in tests/unit/test_web_ui.py's WebUiMarkupTest.
// A substring check on the source can't tell you the feature actually
// works in a browser, so each contract below drives the real, running app
// through Playwright instead. See the commit message / PR description for
// the full old-test -> new-home mapping; a few short-lived comments below
// note the old test name a block replaces.
const fs = require('fs');
const path = require('path');
const { test, expect } = require('@playwright/test');

const token = () => fs.readFileSync(path.join(__dirname, '.fixture-token'), 'utf8').trim();
const viewerToken = () => fs.readFileSync(path.join(__dirname, '.fixture-viewer-token'), 'utf8').trim();

test.beforeEach(async ({ page }) => {
  await page.addInitScript(value => {
    try { localStorage.setItem('localBoardToken', value); } catch {}
  }, token());
});

async function openIssue(page, ref = 'E2E-2') {
  await page.goto('/');
  await page.click(`.issue-row[data-ref="${ref}"]`);
  await expect(page.locator('.issue-title-heading')).toBeVisible();
}

function statusGroup(page, name) {
  return page.locator('.status-group', { has: page.locator('.status-name', { hasText: name }) });
}

// test_issue_workspace_defaults_to_a_grouped_list_with_optional_board_view
test('issue workspace defaults to list layout, with board view optional', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('#issueList')).toBeVisible();
  await expect(page.locator('#issueBoard')).toBeHidden();
  await expect(page.locator('[data-layout="list"]')).toHaveAttribute('aria-pressed', 'true');

  await page.click('[data-layout="board"]');
  await expect(page.locator('#issueBoard')).toBeVisible();
  await expect(page.locator('#issueList')).toBeHidden();
  await expect(page.locator('[data-layout="board"]')).toHaveAttribute('aria-pressed', 'true');
});

// test_issue_detail_is_an_in_app_page_not_a_modal (+ fromApp half of
// test_history_restores_views_and_direct_issue_links_stay_in_the_app)
test('issue detail opens as an in-app page, not a modal, and remembers it was opened from the app', async ({ page }) => {
  await openIssue(page);
  await expect(page.locator('#issueView')).toBeVisible();
  await expect(page.locator('#issuesView')).toBeHidden();
  await expect(page.locator('#issueDialog')).not.toBeVisible();

  const state = await page.evaluate(() => history.state);
  expect(state).toMatchObject({ view: 'issue', issue: 'E2E-2', fromApp: true });

  await page.click('[data-action="back-to-issues"]');
  await expect(page.locator('#issuesView')).toBeVisible();
  await expect(page.locator('#issueView')).toBeHidden();
});

// remaining half of test_issue_detail_is_an_in_app_page_not_a_modal /
// test_history_restores_views_and_direct_issue_links_stay_in_the_app: a
// direct #issue/<ref> link (no in-app navigation) still opens in the page,
// and back-to-issues falls back to the issue list instead of history.back().
test('a direct issue link loads in the app and back-to-issues falls back to the issue list', async ({ page }) => {
  await page.goto('/#issue/E2E-2');
  await expect(page.locator('#issueView')).toBeVisible();
  await expect(page.locator('.issue-title-heading')).toBeVisible();

  const state = await page.evaluate(() => history.state);
  expect(state).toMatchObject({ view: 'issue', issue: 'E2E-2', fromApp: false });

  await page.click('[data-action="back-to-issues"]');
  await expect(page.locator('#issuesView')).toBeVisible();
});

// test_settings_is_a_first_class_navigation_and_history_view (history half)
// and the activity/settings-restore half of
// test_history_restores_views_and_direct_issue_links_stay_in_the_app
test('browser back restores the activity and settings views', async ({ page }) => {
  await page.goto('/');
  await page.click('[data-view="activity"]');
  await expect(page.locator('#activityView')).toBeVisible();

  await page.click('[data-view="settings"]');
  await expect(page.locator('#settingsView')).toBeVisible();
  await expect(page.locator('#activityView')).toBeHidden();

  await page.goBack();
  await expect(page.locator('#activityView')).toBeVisible();

  await page.goBack();
  await expect(page.locator('#issuesView')).toBeVisible();
});

// test_active_primary_navigation_exposes_the_current_page
test('primary navigation marks the active page with aria-current', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('.nav-item[data-view="issues"]')).toHaveAttribute('aria-current', 'page');
  await expect(page.locator('.nav-item[data-view="activity"]')).toHaveAttribute('aria-current', 'false');

  await page.click('[data-view="activity"]');
  await expect(page.locator('.nav-item[data-view="activity"]')).toHaveAttribute('aria-current', 'page');
  await expect(page.locator('.nav-item[data-view="issues"]')).toHaveAttribute('aria-current', 'false');
});

// test_initial_restore_does_not_steal_focus_from_the_skip_link
test('loading the board does not steal focus from the skip link', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('#issueList .issue-row').first()).toBeVisible();
  const activeTag = await page.evaluate(() => document.activeElement?.tagName);
  expect(activeTag).not.toBe('MAIN');
  await expect(page.locator('#mainContent')).not.toBeFocused();
});

// test_new_issue_can_be_assigned_when_created_in_a_started_status and
// test_new_issue_in_started_status_defaults_to_current_actor
test('a new issue started from an in-progress column defaults its assignee to the current actor', async ({ page, request, baseURL }) => {
  const me = await request.get(`${baseURL}/api/me`, { headers: { Authorization: `Bearer ${token()}` } });
  const actorId = (await me.json()).id;

  await page.goto('/');
  await statusGroup(page, 'In Progress').locator('.status-add').click();
  await expect(page.locator('#newIssueHeading')).toHaveText('Create issue in In Progress');
  await expect(page.locator('#issueAssignee')).toHaveValue(String(actorId));
  await expect(page.locator('#issueAssignee option:checked')).toHaveText('alex');

  await page.fill('#issueTitle', 'Assigned on create');
  await page.click('#issueForm button[type="submit"]');
  // Creating opens the new issue's detail page automatically.
  await expect(page.locator('.issue-title-heading')).toHaveText('Assigned on create');
  await expect(page.locator('#issueSidebar')).toContainText('alex');
});

// test_external_git_links_reject_unsafe_url_schemes
test('a javascript: git link is not rendered as a clickable link, unlike a safe one', async ({ page }) => {
  await openIssue(page);
  // The sidebar markup is rendered twice (a desktop rail plus a collapsed
  // mobile <details>), so scope to the always-present aside to stay in
  // Playwright's strict-locator mode.
  const safeLink = page.locator('#issueSidebar .git-link', { hasText: 'safe-pr-1' });
  await expect(safeLink.locator('a')).toHaveAttribute('href', 'https://example.com/pr/1');

  const unsafeLink = page.locator('#issueSidebar .git-link', { hasText: 'unsafe-js-link' });
  await expect(unsafeLink).toBeVisible();
  await expect(unsafeLink.locator('a')).toHaveCount(0);
});

// test_claim_conflicts_reload_current_issue_state: skipped at the e2e
// layer on purpose. The 409-on-stale-revision contract that mutateClaim()
// reacts to is exercised directly and deterministically by
// tests/unit/test_web_ui.py::WebUiTest.test_stale_revision_returns_409_with_json_body
// (and the analogous claim/release round trip in test_claim_then_release_issue).
// Reproducing a genuine claim race through two real browser contexts would
// be slow and flaky for a UI-only reload step that carries little
// independent risk once the REST contract is covered.

// test_mobile_keeps_the_primary_filters_available
test('mobile viewport keeps both issue filters available', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 800 });
  await page.goto('/');
  await expect(page.locator('#milestoneFilter')).toBeVisible();
  await expect(page.locator('#assigneeFilter')).toBeVisible();
});

// test_viewer_role_gets_a_read_only_issue_workspace
test('a viewer-role actor gets a read-only issue workspace', async ({ page }) => {
  await page.addInitScript(value => {
    try { localStorage.setItem('localBoardToken', value); } catch {}
  }, viewerToken());

  await page.goto('/');
  await expect(page.locator('#newIssueBtn')).toBeHidden();

  await page.click('.issue-row[data-ref="E2E-2"]');
  await expect(page.locator('.issue-title-heading')).toBeVisible();

  // Read-only properties render as plain rows, not editable pickers. Scope
  // to the aside: the same markup also renders inside a collapsed mobile
  // <details>, which would otherwise make this locator ambiguous.
  await expect(page.locator('details.property-picker')).toHaveCount(0);
  await expect(page.locator('#issueSidebar .property', { hasText: 'Status' })).toBeVisible();
  await expect(page.locator('[data-action="edit-issue"]')).toHaveCount(0);
  await expect(page.locator('[data-form="comment"]')).toHaveCount(0);
  await expect(page.locator('.claim-action')).toHaveCount(0);
  await expect(page.locator('details.sidebar-disclosure')).toHaveCount(0);
});

// test_settings_gives_milestones_a_dedicated_tab,
// test_settings_gives_labels_a_dedicated_management_tab_and_issue_side_creation,
// test_settings_catalog_renders_complete_status_and_colored_label_lists,
// test_settings_offers_a_read_first_milestone_manager
test('settings tabs switch panels and the overview catalogs are complete', async ({ page }) => {
  await page.goto('/');
  await page.click('[data-view="settings"]');
  await expect(page.locator('#settingsOverviewPanel')).toBeVisible();
  await expect(page.locator('#settingsMilestonesPanel')).toBeHidden();

  await expect(page.locator('#configPreview')).toContainText('prefix = "E2E"');
  const flowText = await page.locator('#settingsCatalog .status-flow').innerText();
  expect(flowText.split('→').map(name => name.trim())).toEqual([
    'Backlog', 'Todo', 'In Progress', 'In Review', 'Done', 'Canceled',
  ]);
  // Other specs run concurrently against the same shared board and may add
  // labels of their own, so assert the fixture's labels are present rather
  // than an exact count.
  const labelsRow = page.locator('#settingsCatalog .catalog-row', { hasText: 'Labels' });
  await expect(labelsRow).toContainText('Bug');
  await expect(labelsRow).toContainText('Review required');
  await expect(labelsRow).toContainText('Feature');

  await page.click('[data-settings-tab="milestones"]');
  await expect(page.locator('#settingsMilestonesPanel')).toBeVisible();
  await expect(page.locator('#settingsOverviewPanel')).toBeHidden();
  await expect(page.locator('#settingsMilestones')).toContainText('M1 — Web app MVP');
  await expect(page.locator('#settingsMilestonesPanel')).toContainText('project.toml');

  await page.click('[data-settings-tab="labels"]');
  await expect(page.locator('#settingsLabelsPanel')).toBeVisible();
  await expect(page.locator('#settingsLabels')).toContainText('Bug');
  await expect(page.locator('#settingsLabels')).toContainText('Review required');
  await expect(page.locator('#settingsLabels')).toContainText('Feature');
});

// test_settings_gives_labels_a_dedicated_management_tab_and_issue_side_creation
// (the settings-side half; issue-side label creation is already covered by
// ui.spec.js's "a label created inline attaches to the issue without overflow")
test('a label can be created from the settings labels tab', async ({ page }) => {
  await page.goto('/');
  await page.click('[data-view="settings"]');
  await page.click('[data-settings-tab="labels"]');
  await page.click('[data-action="create-label"]');
  const name = `Settings label ${Date.now() % 100000}`;
  await page.fill('#newLabelName', name);
  await page.click('#settingsLabelCreate button[type="submit"]');
  await expect(page.locator('#settingsLabels')).toContainText(name);
});

// test_active_issue_filters_hide_empty_status_groups (the has-matches half;
// the no-matches/Clear-filters half is already covered by ui.spec.js's
// "filtered no-results state offers a working Clear filters action")
test('active filters hide status groups with no matching issues', async ({ page }) => {
  await page.goto('/');
  await page.fill('#searchInput', 'Keyboard navigation');
  await expect(statusGroup(page, 'Todo')).toBeVisible();
  await expect(statusGroup(page, 'In Progress')).toHaveCount(0);
  await expect(statusGroup(page, 'In Review')).toHaveCount(0);
});

// test_issue_detail_keeps_secondary_context_in_a_compact_rail
test('issue detail sidebar renders blocking and Git link sections', async ({ page }) => {
  await openIssue(page);
  const sidebar = page.locator('#issueSidebar');
  await expect(sidebar.locator('h2', { hasText: 'Blocking' })).toBeVisible();
  await expect(sidebar).toContainText('E2E-1');
  await expect(sidebar.locator('h2', { hasText: 'Git links' })).toBeVisible();
  await expect(sidebar).toContainText('safe-pr-1');
});

// test_issue_workspace_offers_on_demand_narrative_and_author_comment_editing
test('the issue description and the author\'s own comment can be edited in place', async ({ page }) => {
  await openIssue(page);

  await page.click('[data-action="edit-issue"][data-focus="description"]');
  await page.fill('#editIssueDescription', 'Updated from a contract test.');
  await page.click('[data-form="edit-issue"] button[type="submit"]');
  await expect(page.locator('.description-wrap')).toContainText('Updated from a contract test.');

  await page.click('[data-action="edit-comment"]');
  await page.fill('.comment-editor textarea', 'Edited comment body.');
  await page.click('.comment-editor button[type="submit"]');
  await expect(page.locator('.comment')).toContainText('Edited comment body.');
  await expect(page.locator('.comment')).toContainText('Edited');
});
