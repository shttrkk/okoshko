import assert from "node:assert/strict";
import { chromium } from "playwright";
const base = process.env.OKNO_BASE_URL || "http://localhost:8080";
const browser = await chromium.launch({
  headless: true,
  executablePath:
    process.env.OKNO_BROWSER_EXECUTABLE ||
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
});
let passed = false;
try {
  for (const scheme of ["dark", "light"]) {
    const context = await browser.newContext({
      colorScheme: scheme,
      viewport: { width: 1280, height: 800 },
    });
    const page = await context.newPage();
    await page.route(/https?:\/\/([^/]*\.)?yandex\.[^/]+\//i, (route) =>
      route.abort(),
    );
    await page.route("**/api/**", (route) =>
      route.request().method() === "GET" ? route.continue() : route.abort(),
    );
    await page.goto(base);
    await page.locator(`[data-theme=${scheme}]`).waitFor();
    assert.equal(
      await page.evaluate(() => localStorage.getItem("okno-theme")),
      null,
    );
    const opposite = scheme === "dark" ? "light" : "dark";
    await page.emulateMedia({ colorScheme: opposite });
    await page.locator(`[data-theme=${opposite}]`).waitFor();
    assert.equal(
      await page.evaluate(() => localStorage.getItem("okno-theme")),
      null,
    );
    await page
      .getByRole("button", {
        name:
          scheme === "dark" ? "Включить тёмную тему" : "Включить светлую тему",
        exact: true,
      })
      .click();
    await page.locator(`[data-theme=${scheme}]`).waitFor();
    await page.emulateMedia({ colorScheme: opposite });
    assert.equal(
      await page.locator("[data-theme]").getAttribute("data-theme"),
      scheme,
    );
    await page.reload();
    await page.locator(`[data-theme=${scheme}]`).waitFor();
    await context.close();
  }
  console.log(
    "PASS: fresh system dark/light, live system changes, explicit choice and reload",
  );
  passed = true;
} finally {
  await Promise.race([
    browser.close(),
    new Promise((done) => setTimeout(done, 5000)),
  ]);
  if (passed) process.exit(0);
}
