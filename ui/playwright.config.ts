import { defineConfig } from "@playwright/test";

// The browser tests bring their own API: tests/ui_server.py keeps everything in
// memory, so a run needs neither Docker services nor a model.
const API_PORT = 8010;
const UI_PORT = 3010;

export default defineConfig({
  testDir: "e2e",
  // One shared in-memory API: the tests build on each other's uploads.
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
      command: `uv run python -m tests.ui_server --port ${API_PORT} --ui-origin http://localhost:${UI_PORT} --sample ui/e2e/.tmp/rechnung.pdf`,
      cwd: "..",
      url: `http://127.0.0.1:${API_PORT}/health`,
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: `pnpm exec next dev -H localhost -p ${UI_PORT}`,
      url: `http://localhost:${UI_PORT}`,
      env: { NEXT_PUBLIC_API_URL: `http://127.0.0.1:${API_PORT}` },
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});
