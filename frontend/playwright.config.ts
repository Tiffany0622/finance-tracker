import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './tests/e2e', fullyParallel: false, workers: 1,
  use: {baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:5173',
    channel: process.env.E2E_CHANNEL ?? 'chrome', trace: 'retain-on-failure'},
  projects: [
    {name: 'desktop', use: {viewport: {width: 1440, height: 1050}}},
    {name: 'mobile', use: {viewport: {width: 390, height: 844}, isMobile: true, hasTouch: true}},
  ],
});
