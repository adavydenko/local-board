// @ts-check
const { defineConfig } = require('@playwright/test');

const PORT = 43117;

module.exports = defineConfig({
  testMatch: '*.spec.js',
  timeout: 30_000,
  retries: process.env.CI ? 1 : 0,
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    // Lets a sandboxed environment point at a pre-installed Chromium.
    launchOptions: process.env.PW_CHROMIUM ? { executablePath: process.env.PW_CHROMIUM } : {},
  },
  webServer: {
    command: `python3 serve_fixture.py --port ${PORT}`,
    url: `http://127.0.0.1:${PORT}/health`,
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
