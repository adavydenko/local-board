// @ts-check
const fs = require('fs');
const path = require('path');
const { test, expect } = require('@playwright/test');

const token = () => fs.readFileSync(path.join(__dirname, '.fixture-token'), 'utf8').trim();
const PICKERS = ['status', 'priority', 'assignee_id', 'milestone_id'];

test.beforeEach(async ({ page }) => {
  await page.addInitScript(value => {
    try { localStorage.setItem('localBoardToken', value); } catch {}
  }, token());
});

async function openIssue(page) {
  await page.goto('/');
  await page.click('.issue-row[data-ref="E2E-2"]');
  await expect(page.locator('.issue-title-heading')).toContainText('Verify label creation flow');
}

const expectNoHorizontalOverflow = async page => {
  expect(await page.evaluate(() =>
    document.documentElement.scrollWidth - window.innerWidth
  )).toBeLessThanOrEqual(0);
};

test('pickers never widen the document on a 1280px desktop', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await openIssue(page);
  for (const field of PICKERS) {
    await page.click(`[data-property-picker="${field}"] summary:visible`);
    await expectNoHorizontalOverflow(page);
    await page.keyboard.press('Escape');
  }
  await page.click('[data-property-picker="labels"] summary:visible');
  await expectNoHorizontalOverflow(page);
  await page.click('[data-action="start-create-issue-label"]:visible');
  await expectNoHorizontalOverflow(page);
});

test('pickers never widen the document on a narrow viewport', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 800 });
  await openIssue(page);
  await page.click('.mobile-properties > summary');
  for (const field of PICKERS) {
    await page.click(`[data-property-picker="${field}"] summary:visible`);
    await expectNoHorizontalOverflow(page);
    await page.keyboard.press('Escape');
  }
  await page.click('[data-property-picker="labels"] summary:visible');
  await expectNoHorizontalOverflow(page);
});

test('Escape returns focus to the trigger of the picker that was open', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await openIssue(page);
  for (const field of ['status', 'labels']) {
    await page.click(`[data-property-picker="${field}"] summary:visible`);
    await page.keyboard.press('Escape');
    expect(await page.evaluate(expected => {
      const active = document.activeElement;
      return active?.closest('[data-property-picker]')?.dataset.propertyPicker === expected;
    }, field)).toBe(true);
  }
});

test('filtered no-results state offers a working Clear filters action', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto('/');
  await page.fill('#searchInput', 'nothing-matches-this');
  const clear = page.locator('#issueList [data-action="clear-filters"]');
  await expect(clear).toBeVisible();
  await clear.click();
  await expect(page.locator('#searchInput')).toHaveValue('');
  await expect(page.locator('.issue-row').first()).toBeVisible();
});

test('board view shows card milestones and creates issues per column', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto('/');
  await page.click('[data-layout="board"]');
  const card = page.locator('.board-card[data-ref="E2E-2"]');
  await expect(card.locator('.card-milestone')).toContainText('M1 — Web app MVP');
  await expect(card.locator('.card-blocked')).toHaveText('Blocked');
  const todoColumn = page.locator('.board-column', { has: page.locator('.column-heading', { hasText: 'Todo' }) });
  await todoColumn.locator('[data-new-status="Todo"]').click();
  await expect(page.locator('#newIssueHeading')).toHaveText('Create issue in Todo');
});

test('a label created inline attaches to the issue without overflow', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await openIssue(page);
  await page.click('[data-property-picker="labels"] summary:visible');
  await page.click('[data-action="start-create-issue-label"]:visible');
  const name = `Inline ${Date.now() % 100000}`;
  await page.fill('.label-quick-create input[name="name"]:visible', name);
  await page.click('.label-quick-create button[type="submit"]:visible');
  await expect(page.locator('.assigned-label:visible', { hasText: name })).toBeVisible();
  await expectNoHorizontalOverflow(page);
});
