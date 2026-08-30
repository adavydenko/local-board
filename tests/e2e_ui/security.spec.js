// @ts-check
// Injection/XSS defenses for the web client: the actor's bearer token lives
// in localStorage, so any script an attacker manages to run in the page can
// steal it. Two independent layers guard against that:
//   1. Every render path escapes user-controlled text (dom.js's esc(), used
//      directly or via markdown()) before it reaches innerHTML.
//   2. The server sends a strict Content-Security-Policy on `/` (see
//      local_board/web.py's CSP_POLICY) that blocks inline/injected script
//      execution outright, as a backstop for (1).
//
// This spec seeds its own issue/comment/label carrying classic HTML-injection
// payloads through the real REST API (not the UI form), then asserts the
// payloads show up as literal text everywhere they render and never execute.
// It creates its own entities (uniquely prefixed) rather than reusing the
// shared fixture board's issues, so it can't perturb contracts.spec.js's or
// ui.spec.js's counts/filters, which run against the same live server.
const fs = require('fs');
const path = require('path');
const { test, expect } = require('@playwright/test');

const token = () => fs.readFileSync(path.join(__dirname, '.fixture-token'), 'utf8').trim();

const PREFIX = `XSS-${Date.now()}-${Math.floor(Math.random() * 1e6)}`;
const PAYLOAD_TITLE = `${PREFIX} <img src=x onerror="window.__pwned=1">`;
const PAYLOAD_DESCRIPTION = '<script>window.__pwned=2</script>';
const PAYLOAD_COMMENT = '"><svg onload=window.__pwned=3>';
const PAYLOAD_LABEL = `${PREFIX} <b onmouseover=window.__pwned=4>x</b>`;

/** @type {string} */
let issueIdentifier;

test.describe('injection payloads (own entities, isolated from other specs)', () => {
  test.beforeAll(async ({ request, baseURL }) => {
    const headers = { Authorization: `Bearer ${token()}` };

    const labelResponse = await request.post(`${baseURL}/api/labels`, {
      headers, data: { name: PAYLOAD_LABEL },
    });
    expect(labelResponse.ok(), await labelResponse.text()).toBeTruthy();
    const label = await labelResponse.json();

    const issueResponse = await request.post(`${baseURL}/api/issues`, {
      headers,
      data: { title: PAYLOAD_TITLE, description: PAYLOAD_DESCRIPTION, labels: [label.id] },
    });
    expect(issueResponse.ok(), await issueResponse.text()).toBeTruthy();
    const issue = await issueResponse.json();
    issueIdentifier = issue.identifier;

    const commentResponse = await request.post(`${baseURL}/api/issues/${issueIdentifier}/comments`, {
      headers, data: { body: PAYLOAD_COMMENT },
    });
    expect(commentResponse.ok(), await commentResponse.text()).toBeTruthy();
  });

  test.beforeEach(async ({ page }) => {
    await page.addInitScript(value => {
      try { localStorage.setItem('localBoardToken', value); } catch {}
    }, token());
    // Recorded from inside the page so it survives navigation-triggered
    // script reloads; read back at the end of each test.
    await page.addInitScript(() => {
      window.__cspViolations = [];
      window.addEventListener('securitypolicyviolation', event => {
        window.__cspViolations.push(`${event.violatedDirective}:${event.blockedURI}`);
      });
    });
  });

  test('title, description, comment, and label payloads render as literal text everywhere they appear, and never execute', async ({ page }) => {
    // List view
    await page.goto('/');
    const row = page.locator(`.issue-row[data-ref="${issueIdentifier}"]`);
    await expect(row).toContainText(PAYLOAD_TITLE);
    await expect(row.locator('.label-chip')).toContainText(PAYLOAD_LABEL.slice(0, 20));

    // Board view
    await page.click('[data-layout="board"]');
    const card = page.locator(`.board-card[data-ref="${issueIdentifier}"]`);
    await expect(card).toContainText(PAYLOAD_TITLE);

    // Detail view: title, description, comment body, attached label
    await card.click();
    await expect(page.locator('.issue-title-heading')).toHaveText(PAYLOAD_TITLE);
    await expect(page.locator('.description-wrap')).toContainText(PAYLOAD_DESCRIPTION);
    await expect(page.locator('.comments')).toContainText(PAYLOAD_COMMENT);
    await expect(page.locator('#issueSidebar')).toContainText(PAYLOAD_LABEL);

    // Activity view: the label-created event names the label
    await page.click('[data-view="activity"]');
    await expect(page.locator('#activityFeed')).toContainText(PAYLOAD_LABEL);

    // Settings > Labels tab
    await page.click('[data-view="settings"]');
    await page.click('[data-settings-tab="labels"]');
    await expect(page.locator('#settingsLabels')).toContainText(PAYLOAD_LABEL);

    // None of the onerror/onload/onmouseover/<script> payloads ever ran.
    expect(await page.evaluate(() => window.__pwned)).toBeUndefined();
  });

  test('CSP blocks a freshly injected inline script from executing', async ({ page }) => {
    await page.goto('/');
    // Simulates the payload an attacker's injected markup would need to run
    // even if it slipped past every esc()/markdown() call — the last line
    // of defense is the page's Content-Security-Policy header, not app code.
    const violation = await page.evaluate(() => new Promise(resolve => {
      window.addEventListener('securitypolicyviolation', event => resolve({
        violatedDirective: event.violatedDirective,
        blockedURI: event.blockedURI,
        disposition: event.disposition,
      }), { once: true });
      const script = document.createElement('script');
      script.textContent = 'window.__pwned = 9;';
      document.body.appendChild(script);
    }));
    expect(violation.violatedDirective).toMatch(/^script-src/);
    expect(violation.disposition).toBe('enforce');
    expect(await page.evaluate(() => window.__pwned)).toBeUndefined();
  });

  test('normal navigation across every view raises no CSP violations or console/page errors', async ({ page }) => {
    const consoleErrors = [];
    const pageErrors = [];
    page.on('console', msg => { if (msg.type() === 'error') consoleErrors.push(msg.text()); });
    page.on('pageerror', error => pageErrors.push(String(error)));

    await page.goto('/');
    await expect(page.locator('#issueList .issue-row').first()).toBeVisible();

    await page.click('[data-layout="board"]');
    await expect(page.locator('#issueBoard')).toBeVisible();
    await page.click('[data-layout="list"]');

    await page.click(`.issue-row[data-ref="${issueIdentifier}"]`);
    await expect(page.locator('.issue-title-heading')).toBeVisible();
    await page.click('[data-action="back-to-issues"]');

    await page.click('[data-view="activity"]');
    await expect(page.locator('#activityView')).toBeVisible();

    await page.click('[data-view="settings"]');
    await page.click('[data-settings-tab="milestones"]');
    await page.click('[data-settings-tab="labels"]');
    await expect(page.locator('#settingsLabels')).toContainText(PAYLOAD_LABEL);

    await page.click('[data-view="issues"]');
    await expect(page.locator('#issuesView')).toBeVisible();

    expect(await page.evaluate(() => window.__cspViolations)).toEqual([]);
    const cspConsoleErrors = consoleErrors.filter(text => /content security policy/i.test(text));
    expect(cspConsoleErrors).toEqual([]);
    expect(pageErrors).toEqual([]);
  });
});

test.describe('label color cannot inject CSS into the style attribute', () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(value => {
      try { localStorage.setItem('localBoardToken', value); } catch {}
    }, token());
  });

  test('a non-hex label color is dropped instead of reaching --label-color', async ({ page, request, baseURL }) => {
    // Label colors are actor-controlled and land inside style="". Escaping stops
    // an attribute break-out but not CSS injection within the attribute, so the
    // client emits the custom property only for literal hex values.
    const name = `CSSI-${Date.now()}`;
    const response = await request.post(`${baseURL}/api/labels`, {
      headers: { Authorization: `Bearer ${token()}` },
      data: { name, color: 'red;background:url(https://example.invalid/x.png)' },
    });
    expect(response.ok(), await response.text()).toBeTruthy();

    await page.goto('/');
    await page.click('[data-view="settings"]');
    await page.click('[data-settings-tab="labels"]');
    // Every label row renders the swatch immediately before its name, whether the
    // row is a read-only entry or an expandable editor.
    const swatch = page.locator('#settingsLabels .milestone-name', { hasText: name })
      .locator('xpath=preceding-sibling::span[contains(@class,"label-swatch")]');
    await expect(swatch).toBeVisible();
    // No style attribute at all, so the stylesheet's --label-default applies.
    expect(await swatch.getAttribute('style')).toBeNull();
    expect(await swatch.evaluate(el => getComputedStyle(el).backgroundImage)).toBe('none');
  });

  test('a valid hex label color still reaches --label-color', async ({ page, request, baseURL }) => {
    const name = `HEXOK-${Date.now()}`;
    await request.post(`${baseURL}/api/labels`, {
      headers: { Authorization: `Bearer ${token()}` },
      data: { name, color: '#123abc' },
    });
    await page.goto('/');
    await page.click('[data-view="settings"]');
    await page.click('[data-settings-tab="labels"]');
    // Every label row renders the swatch immediately before its name, whether the
    // row is a read-only entry or an expandable editor.
    const swatch = page.locator('#settingsLabels .milestone-name', { hasText: name })
      .locator('xpath=preceding-sibling::span[contains(@class,"label-swatch")]');
    await expect(swatch).toBeVisible();
    expect(await swatch.getAttribute('style')).toContain('--label-color:#123abc');
    expect(await swatch.evaluate(el => getComputedStyle(el).backgroundColor)).toBe('rgb(18, 58, 188)');
  });
});
