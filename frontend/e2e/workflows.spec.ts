import { expect, test, type Page } from "@playwright/test";

function unique(label: string) {
  return `${label} ${Date.now()}`;
}

async function modal(page: Page) {
  return page.locator(".cds--modal-container:visible").last();
}

test("library create, edit, lock and delete", async ({ page }) => {
  const first = unique("Playwright memory");
  const edited = `${first} edited`;
  await page.goto("/#/library");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Project memory");
  await page.getByRole("button", { name: "New record" }).click();
  const dialog = await modal(page);
  await dialog.locator("#knowledge-title").fill(first);
  await dialog.locator("#knowledge-namespace").selectOption("Decisions");
  await dialog.locator("#knowledge-body").fill("Created by the real browser workflow test.");
  await dialog.locator("#knowledge-tags").fill("e2e, migration");
  await dialog.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText(first, { exact: true })).toBeVisible();

  await page.getByRole("button", { name: new RegExp(first) }).click();
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  const edit = await modal(page);
  await edit.locator("#knowledge-title").fill(edited);
  await edit.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText(edited, { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Lock", exact: true }).click();
  await expect(page.getByRole("button", { name: "Unlock", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Unlock", exact: true }).click();
  page.once("dialog", (confirmation) => void confirmation.accept());
  await page.getByRole("button", { name: "Delete", exact: true }).click();
  await expect(page.getByText(edited, { exact: true })).toHaveCount(0);
});

test("music release, track and artist profile editing", async ({ page }) => {
  const title = unique("Playwright single");
  const edited = `${title} (edit)`;
  await page.goto("/#/music");
  await page.getByRole("button", { name: "New release" }).click();
  let dialog = await modal(page);
  await dialog.locator("#release-title").fill(title);
  await dialog.locator("#release-artist").fill("Test Artist");
  await dialog.locator("#release-notes").fill("Browser fixture release.");
  await dialog.getByRole("button", { name: "Create" }).click();
  await expect(page.getByRole("heading", { name: title, level: 2 })).toBeVisible();

  await page.getByRole("button", { name: "Edit release" }).click();
  dialog = await modal(page);
  await dialog.locator("#release-title").fill(edited);
  await dialog.getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("heading", { name: edited, level: 2 })).toBeVisible();

  await page.locator("#track-title").fill("First track");
  await page.locator("#track-duration").fill("3:21");
  await page.getByRole("button", { name: "Add track", exact: true }).click();
  await expect(page.getByRole("cell", { name: "First track", exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Edit First track" }).click();
  await page.locator("#track-title").fill("First track edited");
  await page.getByRole("button", { name: "Save track" }).click();
  await expect(page.getByRole("cell", { name: "First track edited", exact: true })).toBeVisible();

  page.once("dialog", (confirmation) => void confirmation.accept());
  await page.getByRole("button", { name: "Delete First track edited" }).click();
  await expect(page.getByRole("cell", { name: "First track edited", exact: true })).toHaveCount(0);

  await page.getByRole("button", { name: "Artist profile" }).click();
  dialog = await modal(page);
  await dialog.locator("#profile-name").fill("NOVA Test Artist");
  await dialog.getByRole("button", { name: "Save profile" }).click();
  await expect(page.getByText(/Artist profile: NOVA Test Artist/)).toBeVisible();

  page.once("dialog", (confirmation) => void confirmation.accept());
  await page.getByRole("button", { name: "Delete", exact: true }).click();
  await expect(page.getByRole("heading", { name: edited, level: 2 })).toHaveCount(0);
});

test("mission objective and queue editing", async ({ page }) => {
  const objective = unique("Browser objective");
  const item = unique("Browser item");
  await page.goto("/#/mission");
  await page.getByRole("button", { name: /(Edit|Set) objective/ }).click();
  let dialog = await modal(page);
  await dialog.locator("#objective-title").fill(objective);
  await dialog.locator("#objective-detail").fill("Objective written by Playwright.");
  await dialog.getByRole("button", { name: "Save objective" }).click();
  await expect(page.getByRole("heading", { name: objective, level: 3 })).toBeVisible();

  await page.getByRole("button", { name: "New item" }).click();
  dialog = await modal(page);
  await dialog.locator("#item-title").fill(item);
  await dialog.locator("#item-detail").fill("Queue item written by Playwright.");
  await dialog.getByRole("button", { name: "Save item" }).click();
  await expect(page.getByText(item, { exact: true })).toBeVisible();

  await page.getByRole("button", { name: `Edit ${item}` }).click();
  dialog = await modal(page);
  await dialog.locator("#item-state").selectOption("ready");
  await dialog.getByRole("button", { name: "Save item" }).click();
  const row = page.locator(".nova-list-row").filter({ hasText: item });
  await expect(row.getByText("ready", { exact: true })).toBeVisible();
});

test("settings persist and release controls are guarded", async ({ page }) => {
  await page.goto("/#/settings");
  await page.locator("#set-idle").fill("21");
  await page.getByRole("button", { name: "Save settings" }).click();
  await expect(page.getByText("Settings saved on the server.")).toBeVisible();
  await page.reload();
  await expect(page.locator("#set-idle")).toHaveValue("21");

  await page.locator("#gh-token").fill("github_pat_playwright_fixture_token_1234567890");
  await page.getByRole("button", { name: "Save token" }).click();
  await expect(page.getByText(/Token saved/)).toBeVisible();

  await page.goto("/#/release");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Versions and rollback");
  await expect(page.getByRole("button", { name: /Install version/ })).toBeDisabled();
});

test("chat streams, accepts an attachment, renames and reopens a message", async ({ page }) => {
  const title = unique("Browser conversation");
  const response = await page.request.post("/autopilot/api/conversations", {
    headers: { "Content-Type": "application/json", "X-Vellur-Request": "1" },
    data: { title },
  });
  expect(response.ok()).toBeTruthy();
  const conversation = (await response.json()) as { id: number };
  await page.goto(`/#/chat/${conversation.id}`);

  await page.locator('input[type="file"]').setInputFiles({
    name: "evidence.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("attachment from Playwright"),
  });
  await expect(page.getByText("evidence.txt")).toBeVisible();
  await page.locator("#nova-chat-input").fill("Summarize the attached evidence.");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.locator(".nova-message.is-user").last()).toContainText("Summarize the attached evidence.");
  await expect(page.locator(".nova-message.is-assistant").last()).toContainText("staging preview answer", { timeout: 30_000 });

  await page.getByRole("button", { name: "Edit and resend" }).last().click();
  await expect(page.locator("#nova-chat-input")).toHaveValue("Summarize the attached evidence.");

  await page.getByRole("button", { name: "Rename conversation" }).click();
  await page.locator("#chat-title").fill(`${title} renamed`);
  await page.getByRole("button", { name: "Save title" }).click();
  await expect(page.getByRole("heading", { level: 2 })).toContainText(`${title} renamed`);
});

test("3D Studio stays available inside the React shell", async ({ page }) => {
  await page.goto("/#/studio");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Scenes");
  const frame = page.locator("iframe[title='NOVA 3D Studio']");
  await expect(frame).toBeVisible();
  await expect(frame).toHaveAttribute("src", "/autopilot/3d/");
});

test("agent task creation streams runner events to completion", async ({ page }) => {
  const prompt = unique("Browser agent task");
  await page.goto("/#/agent/new");
  await page.locator("#task-prompt").fill(prompt);
  await page.getByRole("button", { name: "Start task" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toContainText(prompt);
  await expect(page.getByText(/Demo simulation finished/)).toBeVisible({ timeout: 20000 });
});
