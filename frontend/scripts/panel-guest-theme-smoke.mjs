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
    await context.addInitScript(() =>
      localStorage.setItem("okno-theme", "dark"),
    );
    const page = await context.newPage();
    await page.route(/yandex\.|ya\.ru|mapillary/i, (route) => route.abort());
    await page.route("**/api/**", (route) =>
      route.request().method() === "GET" ? route.continue() : route.abort(),
    );
    await page.goto(base);
    await page.locator("[data-theme=light]").waitFor();
    await page.emulateMedia({
      colorScheme: scheme === "dark" ? "light" : "dark",
    });
    assert.equal(
      await page.locator("[data-theme]").getAttribute("data-theme"),
      "light",
    );
    assert.equal(await page.locator(".theme-toggle").count(), 0);
    await context.close();
  }
  console.log(
    "PASS: light theme only for dark/light systems and a stored dark choice; no toggle",
  );
  passed = true;
} finally {
  await Promise.race([
    browser.close(),
    new Promise((done) => setTimeout(done, 5000)),
  ]);
  if (passed) process.exit(0);
}
