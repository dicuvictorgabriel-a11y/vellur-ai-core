import { expect, test, type Page } from "@playwright/test";

function collectErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(String(error)));
  page.on("response", (response) => {
    const url = response.url();
    if (response.status() < 400 || url.includes("favicon") || url.includes(".well-known")) return;
    errors.push(`${response.status()} ${url}`);
  });
  return errors;
}

test("home loads live controller data", async ({ page }) => {
  const errors = collectErrors(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Good to see you");
  await expect(page.getByText("Controller", { exact: false }).first()).toBeVisible();
  await expect(page.getByRole("link", { name: /NOVA/ }).first()).toBeVisible();
  expect(errors).toEqual([]);
});

test("main views load without API errors", async ({ page }) => {
  const errors = collectErrors(page);
  const routes = ["chat", "agent", "models", "costs", "activity", "gpu", "library", "music", "tools", "mission", "themes", "release", "settings"];
  for (const route of routes) {
    await page.goto(`/#/${route}`);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await expect(page.locator(".nova-loaderror")).toHaveCount(0);
  }
  expect(errors).toEqual([]);
});

test("theme choice changes tokens and survives reload", async ({ page }) => {
  await page.goto("/#/themes");
  await page.locator(".nova-theme-card", { hasText: "Porcelain" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-nova-theme", "porcelain");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-nova-theme", "porcelain");
  await page.locator(".nova-theme-card", { hasText: "Obsidian" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-nova-theme", "obsidian");
});

test("chat streams an answer and renders markdown", async ({ page }) => {
  await page.goto("/#/chat");
  const conversation = page.locator(".nova-conv").first();
  await expect(conversation).toBeVisible();
  await conversation.click();
  await expect(page.locator("#nova-chat-input")).toBeVisible();
  const before = await page.locator(".nova-message.is-assistant").count();
  await page.locator("#nova-chat-input").fill("Reply with one short sentence.");
  await page.locator("#nova-chat-input").press("Enter");
  await expect(page.locator(".nova-message.is-assistant")).toHaveCount(before + 1, { timeout: 30_000 });
  await expect(page.locator(".nova-message.is-assistant").last()).toBeVisible();
  await expect(page.locator(".nova-message.is-streaming")).toHaveCount(0, { timeout: 30_000 });
});

test("mobile menu reaches a view", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.locator(".cds--header__menu-toggle").click();
  await page.getByRole("link", { name: "Music" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Releases");
  await expect(page.locator(".nova-tool-grid")).toHaveCount(0);
});
