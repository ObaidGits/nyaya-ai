import { expect, test } from "@playwright/test";

/**
 * Browser E2E against the live docker stack (§22 of the remediation
 * directive): a real Chromium drives the real frontend -> API -> retrieval
 * -> live LLM (Ollama qwen2.5:7b) path. No TestClient, no mocks.
 *
 * Run: npx playwright test --config e2e/playwright.config.ts
 * Prereq: docker compose up (frontend on 127.0.0.1:3000, API on :8000).
 */

const FRONTEND = process.env.E2E_BASE_URL ?? "http://localhost:3000";

test.describe("live chat E2E", () => {
  test("grounded answer streams with citations", async ({ page }) => {
    await page.goto(FRONTEND);
    await expect(page.getByRole("log")).toBeVisible();

    const input = page.getByPlaceholder("Ask a legal question…");
    await input.fill("What does section 303 of the BNS say?");
    await input.press("Enter");

    // The answer streams into the conversation log.
    const log = page.getByRole("log");
    await expect
      .poll(async () => log.textContent(), { timeout: 120_000 })
      .toContain("theft", { ignoreCase: true });
  });

  test("off-corpus question is refused in the browser", async ({ page }) => {
    await page.goto(FRONTEND);
    const input = page.getByPlaceholder("Ask a legal question…");
    await input.fill("What is the capital of France?");
    await input.press("Enter");

    const log = page.getByRole("log");
    await expect
      .poll(async () => log.textContent(), { timeout: 120_000 })
      .toMatch(/don't know|cannot|not.*source|no.*citation/i);
  });
});
