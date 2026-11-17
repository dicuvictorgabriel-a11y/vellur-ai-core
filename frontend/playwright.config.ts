import { defineConfig } from "@playwright/test";

const repo = new URL("..", import.meta.url).pathname;
const authPassword = "preview-temporary-password";

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  fullyParallel: false,
  reporter: "list",
  use: {
    baseURL: process.env.NOVA_URL || "http://127.0.0.1:5173",
    trace: "off",
    screenshot: "only-on-failure",
  },
  webServer: [
    {
      command: `cd ${repo} && PORT=4173 bash dev/preview.sh`,
      url: "http://127.0.0.1:4173/healthz",
      reuseExistingServer: true,
      timeout: 120_000,
    },
    {
      command: `cd ${repo} && PREVIEW_REAL_AUTH=1 PREVIEW_AUTH_PASSWORD=${authPassword} PORT=4180 bash dev/preview.sh`,
      url: "http://127.0.0.1:4180/healthz",
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: "npm run dev -- --port 5173 --strictPort",
      url: "http://127.0.0.1:5173",
      reuseExistingServer: true,
      timeout: 120_000,
    },
  ],
});
