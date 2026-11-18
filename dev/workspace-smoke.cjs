const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const fs = require("fs");
const assert = require("node:assert/strict");
fs.mkdirSync("dev/evidence", { recursive: true });
// React-era journey: the same shape as the legacy smoke run (desktop + mobile evidence, no overflow,
// no page errors), but driving the React app through its hash routes.
(async () => {
  const base = "http://127.0.0.1:4173/autopilot/";
  const b = await chromium.launch({
    ...(process.env.CHROMIUM ? { executablePath: process.env.CHROMIUM } : {}),
    headless: true,
    args: ["--no-sandbox", "--enable-unsafe-swiftshader"],
  });
  const p = await b.newPage({ viewport: { width: 1672, height: 941 } });
  const errors = [];
  p.on("pageerror", (e) => errors.push(e.message));
  p.on("dialog", (d) => d.accept());
  async function shot(name) {
    await p.waitForTimeout(700);
    await p.screenshot({ path: "dev/evidence/" + name + ".png" });
    assert.equal(await p.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, name + " overflows");
    console.log("PASS", name);
  }
  const views = ["home", "chat", "agent", "library", "music", "tools", "mission", "projects", "models", "costs", "activity", "gpu", "release", "settings"];
  await p.goto(base);
  await p.getByRole("heading", { level: 1 }).first().waitFor({ timeout: 30000 });
  await shot("workspace-desktop");
  for (const v of views.slice(1)) {
    await p.goto(base + "#/" + v);
    await p.getByRole("main").waitFor();
    await shot("view-" + v);
  }
  // deep link straight into a nested route (fresh load, no prior client state)
  await p.goto(base + "#/gpu");
  await p.getByRole("heading", { level: 1 }).first().waitFor();
  await shot("deep-link-gpu");
  await p.setViewportSize({ width: 390, height: 844 });
  await p.goto(base + "#/chat");
  await shot("conversation-mobile");
  await p.setViewportSize({ width: 820, height: 1180 });
  await p.goto(base + "#/home");
  await shot("workspace-tablet");
  assert.deepEqual(errors, []);
  await b.close();
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
