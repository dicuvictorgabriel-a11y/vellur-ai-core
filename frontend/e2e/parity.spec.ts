import { expect, test } from "@playwright/test";

test("models: routing mode saves, route order edits, prices save", async ({ page }) => {
  await page.goto("/#/models");
  await expect(page.getByRole("heading", { name: "Routing policy" })).toBeVisible();

  await page.getByRole("radio", { name: "economy" }).click();
  await expect(page.getByText("Routing policy saved.")).toBeVisible();
  await expect(page.locator(".nova-seg button.is-on")).toHaveText("economy");

  await page.getByRole("radio", { name: "auto" }).click();
  await expect(page.getByText("Routing policy saved.")).toBeVisible();
  await expect(page.locator("#nova-auto-allowance")).toBeVisible();

  const imageRole = page.locator(".nova-route-role").filter({ has: page.locator("legend", { hasText: "vellur-image" }) });
  await imageRole.locator("select").selectOption("preview:demo-chat");
  await imageRole.getByRole("button", { name: "Add to vellur-image" }).click();
  const saveRouting = page.getByRole("button", { name: "Save routing" });
  await expect(saveRouting).toBeEnabled();
  await imageRole.locator(".nova-route-chip").filter({ hasText: "preview:demo-chat" }).getByRole("button", { name: "Move up" }).click();
  await imageRole.locator(".nova-route-chip").nth(0).locator("code, .nova-route-chip-name").first();
  await expect(imageRole.locator(".nova-route-chip").first()).toContainText("preview:demo-chat");
  await saveRouting.click();
  await expect(page.getByText("Routes saved. New messages use the updated order.")).toBeVisible();

  await imageRole.locator(".nova-route-chip").filter({ hasText: "preview:demo-chat" }).getByRole("button", { name: "Remove preview:demo-chat" }).click();
  await saveRouting.click();
  await expect(imageRole.locator(".nova-route-chip").first()).toContainText("preview:demo-image");
  await expect(page.getByText("Routes saved. New messages use the updated order.")).toBeVisible();

  await expect(page.getByRole("heading", { name: "Providers and keys" })).toBeVisible();
  await expect(page.locator("table").first()).toContainText("preview");

  await expect(page.getByRole("heading", { name: "Models and capabilities" })).toBeVisible();
  await page.locator("#nova-catalog-filter").fill("demo-image");
  await expect(page.locator(".nova-catalog-group").first()).toContainText("preview:demo-image");
  await page.locator("#nova-catalog-filter").fill("nothing-matches-this");
  await expect(page.getByText("No models match.")).toBeVisible();
  await page.locator("#nova-catalog-filter").fill("");

  await expect(page.getByText(/Image generation: available/)).toBeVisible();
  await page.locator("#nova-manual-prices").fill("preview:demo-chat 0 0");
  await page.getByRole("button", { name: "Save prices" }).click();
  await expect(page.getByText("Prices and image limits saved.")).toBeVisible();
});

test("models: test call shows result", async ({ page }) => {
  await page.goto("/#/models");
  await expect(page.getByRole("heading", { name: "Providers and keys" })).toBeVisible();
  await page.locator("#nova-test-target").selectOption("preview:demo-chat");
  await page.getByRole("button", { name: "Test call" }).click();
  await expect(page.getByText("Test preview:demo-chat done.")).toBeVisible({ timeout: 20_000 });
});

test("costs: fixed costs editor saves and reloads", async ({ page }) => {
  await page.goto("/#/costs");
  await expect(page.getByRole("heading", { name: "Fixed costs" })).toBeVisible();
  const field = page.locator("#nova-fixed-costs");
  await field.fill("Domain | 12.00 | no\nRent | 0.25 | yes");
  await page.getByRole("button", { name: "Save fixed costs" }).click();
  await expect(page.getByText("Fixed costs saved.")).toBeVisible();

  await page.reload();
  await expect(page.locator("#nova-fixed-costs")).toHaveValue(/Domain \| 12(\.0+)? \| no/);
  await expect(page.locator("#nova-fixed-costs")).toHaveValue(/Rent \| 0\.25 \| yes/);
});

test("chat: message images open a full-size lightbox", async ({ page }) => {
  await page.goto("/#/chat");
  await page.locator(".nova-conv-list a").filter({ hasText: "Demo conversation" }).first().click();
  const image = page.locator("img.nova-message-image.is-zoomable").first();
  await expect(image).toBeVisible();
  await image.click();
  const lightbox = page.locator(".nova-lightbox");
  await expect(lightbox).toBeVisible();
  await expect(lightbox.locator("img")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(lightbox).toBeHidden();
});

test("chat: image attachments zoom before opening the file", async ({ page }) => {
  await page.goto("/#/chat");
  await page.locator(".nova-conv-list a").filter({ hasText: "Demo conversation" }).first().click();
  const thumb = page.locator("img.nova-attachment-thumb").first();
  const anyThumb = await thumb.count();
  test.skip(anyThumb === 0, "no image attachment in the demo conversation");
  await thumb.click();
  await expect(page.locator(".nova-lightbox")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.locator(".nova-lightbox")).toBeHidden();
});

test("music: cover picker sets the release cover", async ({ page }) => {
  await page.goto("/#/music");
  await page.locator(".nova-project-list a").first().click();
  const choose = page.getByRole("button", { name: "Choose cover" });
  await expect(choose).toBeVisible();
  await choose.click();
  const picker = page.locator(".nova-cover-picker");
  await expect(picker).toBeVisible({ timeout: 10_000 });
  const images = await picker.locator("button").count();
  test.skip(images === 0, "no library images in the demo data");
  await picker.locator("button").first().click();
  await expect(page.locator(".nova-cover-wrap")).toBeVisible();
  await expect(page.locator(".nova-cover-blend")).toBeVisible();
});
