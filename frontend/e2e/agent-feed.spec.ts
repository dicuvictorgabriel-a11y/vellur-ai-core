import { expect, test } from "@playwright/test";

// iPhone-sized viewport: the live feed must not move the reader, and long output must not widen the page.
test.use({ ...(process.env.PW_CHROMIUM ? { launchOptions: { executablePath: process.env.PW_CHROMIUM } } : {}), viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });

test("agent feed keeps the reading position, follows only at the bottom, readable logs", async ({ page }) => {
  let next = 1;
  const long = "npm run build -- --verbose " + "x".repeat(400);
  const make = (n: number) =>
    Array.from({ length: n }, () => {
      const id = next++;
      return { id, ts: 1_700_000_000 + id, run: 1, kind: "tool", data: { tool: "bash", input: { command: id % 3 ? `step ${id}` : long }, output: `line ${id}` } };
    });
  await page.route("**/autopilot/api/agent/tasks/7", (r) =>
    r.fulfill({ json: { id: 7, state: "running", prompt: "Feed test", repo: "o/r", model: "vellur-coder", runs: 1 } }));
  await page.route("**/autopilot/api/agent/tasks/7/delivery", (r) => r.fulfill({ json: { stages: [] } }));
  const all = make(150);
  await page.route(/\/autopilot\/api\/agent\/tasks\/7\/events/, (r) => {
    const after = Number(new URL(r.request().url()).searchParams.get("after") || 0);
    all.push(...make(3));                         // the agent keeps producing events
    return r.fulfill({ json: { events: all.filter((e) => e.id > after), task: { id: 7, state: "running" }, has_more: false } });
  });
  await page.goto("/#/agent/7");
  const feed = page.getByTestId("agent-feed");
  await expect(feed.locator("li").first()).toBeVisible();
  // follows the newest events while at the bottom
  await expect.poll(() => feed.evaluate((el) => el.scrollHeight - el.scrollTop - el.clientHeight)).toBeLessThan(48);
  // the reader scrolls up: position stays put through several polls, and the page itself never jumps
  await feed.evaluate((el) => { el.scrollTop = 200; el.dispatchEvent(new Event("scroll")); });
  await expect(page.getByRole("button", { name: "Jump to latest" })).toBeVisible();
  const pageY = await page.evaluate(() => window.scrollY);
  const before = await feed.locator("li").count();
  await expect.poll(() => feed.locator("li").count(), { timeout: 10_000 }).toBeGreaterThan(before + 3);
  expect(await feed.evaluate((el) => el.scrollTop)).toBe(200);
  expect(await page.evaluate(() => window.scrollY)).toBe(pageY);
  // long output scrolls inside its box, the page never gets wider than the phone
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  const size = await feed.locator(".nova-pre").first().evaluate((el) => parseFloat(getComputedStyle(el).fontSize));
  expect(size).toBeGreaterThanOrEqual(13);
  await feed.scrollIntoViewIfNeeded();
  await page.screenshot({ path: "../docs/agent-feed-mobile.png" });
  // Jump to latest restores live-follow
  await page.getByRole("button", { name: "Jump to latest" }).click();
  await expect.poll(() => feed.evaluate((el) => el.scrollHeight - el.scrollTop - el.clientHeight)).toBeLessThan(48);
  await expect(page.getByRole("button", { name: "Jump to latest" })).toHaveCount(0);
});
