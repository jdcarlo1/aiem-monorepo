import { defineConfig, devices } from "@playwright/test";

const TEST_PORT = 5174;
const externalBase = process.env.PLAYWRIGHT_BASE_URL;

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? "github" : "list",

  use: {
    baseURL: externalBase ?? `http://localhost:${TEST_PORT}/aiem/`,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },

  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],

  webServer: externalBase
    ? undefined
    : {
        command: `PORT=${TEST_PORT} BASE_PATH=/aiem/ pnpm run dev`,
        port: TEST_PORT,
        reuseExistingServer: true,
        timeout: 60_000,
        env: {
          PORT: String(TEST_PORT),
          BASE_PATH: "/aiem/",
        },
      },
});
