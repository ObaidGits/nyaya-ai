import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: ".",
  timeout: 180_000,
  retries: 0,
  workers: 1,
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
  },
});
