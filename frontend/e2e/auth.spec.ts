import { expect, test } from "@playwright/test";

const AUTH = "http://127.0.0.1:4180/autopilot/react/";

test("real login, forced password change and session shell", async ({ page }) => {
  await page.goto(AUTH);
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await expect(page.locator(".nova-shell")).toHaveCount(0);
  await page.locator("#nova-password").fill("preview-temporary-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Change password" })).toBeVisible();
  await page.locator("#current-password").fill("preview-temporary-password");
  await page.locator("#new-password").fill("browser-new-password");
  await page.locator("#confirm-password").fill("browser-new-password");
  await page.getByRole("button", { name: "Save password" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toContainText(/Good (morning|afternoon|evening)/, { timeout: 30_000 });
  await page.reload();
  await expect(page.getByRole("heading", { level: 1 })).toContainText(/Good (morning|afternoon|evening)/);
  await expect(page.getByRole("heading", { name: "Sign in" })).toHaveCount(0);
});
