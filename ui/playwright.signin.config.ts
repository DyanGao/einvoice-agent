import { defineConfig } from "@playwright/test";

// The review UI with sign-in on (AUTH=on): its own API with test accounts and a
// stand-in identity provider (tests/ui_server.py --auth on). Both servers are
// on "localhost", so the session cookie (SameSite=Strict) travels between them.
const API_PORT = 8011;
const UI_PORT = 3011;

export default defineConfig({
  testDir: "e2e",
  testMatch: "sign-in.spec.ts",
  workers: 1,
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: process.env.CI ? "line" : "list",
  use: {
    baseURL: `http://localhost:${UI_PORT}`,
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command: `uv run python -m tests.ui_server --auth on --port ${API_PORT} --ui-origin http://localhost:${UI_PORT} --sample ui/e2e/.tmp-signin/rechnung.pdf`,
      cwd: "..",
      url: `http://127.0.0.1:${API_PORT}/health`,
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: `pnpm exec next dev -H localhost -p ${UI_PORT}`,
      url: `http://localhost:${UI_PORT}`,
      env: { NEXT_PUBLIC_API_URL: `http://localhost:${API_PORT}` },
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});
