import { defineConfig, devices } from "@playwright/test"

const installedChrome = process.env.PLAYWRIGHT_CHROME_EXECUTABLE
const launchOptions = installedChrome ? { executablePath: installedChrome } : undefined

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  workers: 2,
  retries: 1,
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:4187",
    trace: "on-first-retry",
    launchOptions,
  },
  projects: [
    { name: "desktop-chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } },
    { name: "mobile-chromium", use: { ...devices["Pixel 7"], viewport: { width: 390, height: 844 } } },
  ],
  webServer: {
    command: "npm run preview -- --host 127.0.0.1 --port 4187",
    url: "http://127.0.0.1:4187",
    reuseExistingServer: true,
    timeout: 120000,
  },
})

