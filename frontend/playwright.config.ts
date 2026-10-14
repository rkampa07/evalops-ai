import { defineConfig, devices } from "@playwright/test";
import { API_URL, WEB_URL, API_PORT, WEB_PORT } from "./e2e/environment";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  // Retries are opt-in for diagnosis; CI must surface first-run failures.
  retries: 0,
  forbidOnly: !!process.env.CI,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: WEB_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    locale: "en-US",
    timezoneId: "UTC",
  },
  projects: [
    { name: "seed", testMatch: /seed\.setup\.ts/ },
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
      dependencies: ["seed"],
      testMatch: /.*\.spec\.ts/,
    },
  ],
  webServer: [
    {
      command: "../backend/.venv/bin/python e2e/start_backend.py",
      url: `${API_URL}/health`,
      reuseExistingServer: false,
      timeout: 60_000,
      env: {
        EVALOPS_E2E_API_PORT: String(API_PORT),
        EVALOPS_E2E_WEB_PORT: String(WEB_PORT),
      },
    },
    {
      command: `npm run dev -- --host 127.0.0.1 --port ${WEB_PORT} --strictPort`,
      url: WEB_URL,
      reuseExistingServer: false,
      timeout: 60_000,
      env: { VITE_API_BASE_URL: API_URL },
    },
  ],
});
