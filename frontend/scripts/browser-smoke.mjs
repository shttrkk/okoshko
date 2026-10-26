import { chromium } from "playwright";
import { existsSync } from "node:fs";
import assert from "node:assert/strict";

const base = process.env.OKNO_BASE_URL || "http://localhost:8080";
const installedChrome =
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const executablePath =
  process.env.OKNO_BROWSER_EXECUTABLE ||
  (existsSync(installedChrome) ? installedChrome : undefined);
const browser = await chromium.launch({ headless: true, executablePath });
try {
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1000 },
  });
  const errors = [],
    failures = [],
    requests = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("response", (response) => {
    // Linked official-site photos are optional and fall back to a model view.
    if (response.status() >= 400 && response.url().startsWith(base))
      failures.push(`${response.status()} ${response.url()}`);
  });
  page.on("request", (request) => requests.push(request.url()));
  await page.goto(base);
  await page.locator('[data-map-status="ready"]').waitFor({ timeout: 45000 });
  await page.locator(".map-marker").first().waitFor({ timeout: 45000 });
  const status = await (await page.request.get(`${base}/api/status`)).json();
  assert(status.hotel_count > 0, "The seeded hotel catalog must be ready");
  await page.waitForFunction(
    (count) =>
      document
        .querySelector(".hero-meta")
        ?.textContent.includes(`${count} отелей`),
    status.hotel_count,
  );
  assert.match(
    await page
      .locator('[data-testid="travel-map"]')
      .getAttribute("data-map-source"),
    /PMTiles/,
  );
  assert(
    requests.some((url) => /maplibre-gl-worker.*\.mjs/.test(url)),
    "The pinned MapLibre worker must be emitted and requested",
  );
  assert(
    !requests.some((url) => /\/seed\/(buildings|roads)\.geojson/.test(url)),
    "PMTiles must not download full regional fallback GeoJSON",
  );
  await page.getByTestId("search-submit").click();
  await page.locator(".result-card").first().waitFor({ timeout: 45000 });
  await page.getByLabel(/^Порог: До (пляжа|моря)/).fill("8");
  const updated = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/search") &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Применить", exact: true }).click();
  const data = await (await updated).json();
  assert.equal(
    data.parsed.must.find(
      (criterion) => criterion.characteristic_id === "beach_walk",
    ).value,
    8,
  );
  await page.getByTestId("open-hotel-0").click();
  await page.locator(".hotel-title-row h1").waitFor({ timeout: 45000 });
  assert(
    (await page.locator(".hotel-fit .verdict-list li").count()) > 0,
    "Hotel must evaluate the active parsed query",
  );
  await page.locator(".evidence-button").first().click();
  await page.getByRole("dialog").waitFor();
  assert.match(
    await page.getByRole("dialog").innerText(),
    /Данные получены[\s\S]*Актуально до/,
  );
  await page.keyboard.press("Escape");
  await page.getByRole("dialog").waitFor({ state: "hidden" });
  await page.goto(
    `${base}/search?q=${encodeURIComponent("тихий номер в Казани")}`,
  );
  await page.getByTestId("region-limitation").waitFor({ timeout: 45000 });
  assert.equal(await page.locator(".result-card").count(), 0);
  const mobile = await browser.newPage({
    viewport: { width: 390, height: 844 },
    isMobile: true,
    hasTouch: true,
  });
  await mobile.goto(base);
  await mobile.getByTestId("search-submit").waitFor();
  assert.equal(
    await mobile.evaluate(() => document.documentElement.scrollWidth),
    390,
    "Mobile layout must not overflow",
  );
  await mobile.getByRole("button", { name: "Меню", exact: true }).click();
  await mobile.locator(".mobile-nav").waitFor();
  assert.deepEqual(errors, [], "No uncaught browser errors");
  assert.deepEqual(failures, [], "No broken production assets or API requests");
  console.log(
    JSON.stringify(
      {
        status: "passed",
        base,
        checks: [
          "bundled-worker",
          "PMTiles-primary",
          "live-search-threshold",
          "parsed-hotel",
          "evidence-dialog",
          "region-limit",
          "mobile-navigation",
        ],
        requests: requests.length,
      },
      null,
      2,
    ),
  );
} finally {
  await browser.close();
}
