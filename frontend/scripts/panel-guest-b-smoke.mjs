import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { resolve } from "node:path";
import { chromium } from "playwright";

// Read/search only on the shared API. Browser searches are fixture responses;
// every other write is blocked before navigation.
const base = process.env.OKNO_BASE_URL || "http://localhost:8080";
const apiBase = process.env.OKNO_API_BASE_URL || base;
const artifacts = resolve(
  process.env.OKNO_PANEL_GUEST_ARTIFACTS ||
    fileURLToPath(new URL("../../runtime/panel-guest-b", import.meta.url)),
);
await mkdir(artifacts, { recursive: true });
const response = await fetch(`${apiBase}/api/search`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ text: "вид на море, тихий номер", limit: 8 }),
});
assert(response.ok);
const seed = await response.json();
assert(seed.results.length >= 4);
const browser = await chromium.launch({
  headless: true,
  executablePath:
    process.env.OKNO_BROWSER_EXECUTABLE ||
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
});
let passed = false;
const errors = [],
  blocked = [];
try {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
    colorScheme: "light",
  });
  const page = await context.newPage();
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route(/https?:\/\/([^/]*\.)?yandex\.[^/]+\//i, (route) => {
    blocked.push(route.request().url());
    return route.abort();
  });
  const hit = (index, group, status, recommendation) => ({
    ...seed.results[index],
    group,
    must_total: 1,
    must_satisfied: status === "yes" ? 1 : 0,
    room_plan: [],
    room_recommendation: recommendation,
    verdicts: [
      {
        ...seed.results[index].verdicts[0],
        characteristic_id: "sea_view",
        label: "Вид на море",
        required: true,
        status,
        text: "Вид на море по расчёту",
        symbol:
          status === "yes"
            ? "✓"
            : status === "partial"
              ? "≈"
              : status === "no"
                ? "✗"
                : "?",
        synthetic: false,
        hotelier_confirmed: false,
        basis: "calculation",
        caveat: null,
        disputed: false,
      },
    ],
  });
  let results = [
    hit(0, "all", "partial", "Лучший вариант: западная сторона, 3-й этаж."),
    hit(1, "almost", "partial", "Лучший вариант: южная сторона, 4-й этаж."),
    hit(2, "unknown", "partial", "Лучший вариант: западная сторона, 5-й этаж."),
    hit(
      3,
      "almost",
      "no",
      "Номер со всеми обязательными условиями не найден в имеющихся данных.",
    ),
  ];
  await page.route("**/api/**", (route) => {
    const request = route.request(),
      path = new URL(request.url()).pathname;
    if (path === "/api/search")
      return route.fulfill({
        json: { ...seed, results, total: results.length },
      });
    if (request.method() !== "GET") {
      blocked.push(`${request.method()} ${path}`);
      return route.abort();
    }
    return route.continue();
  });
  await page.goto(`${base}/search?q=panel-b-public-fixture`);
  await page.locator(".result-card").first().waitFor();
  assert.equal(await page.locator(".result-card").count(), 4);
  assert.equal(await page.getByTestId("partial-legend").count(), 1);
  const groupNotes = await page.locator(".result-group").evaluateAll((groups) =>
    groups.map((group) => ({
      legend: group.querySelectorAll('[data-testid="partial-legend"]').length,
      notes: [
        ...group.querySelectorAll(
          ':scope > .result-group-note:not([data-testid="partial-legend"])',
        ),
      ].map((note) => note.textContent.trim()),
    })),
  );
  assert.equal(groupNotes.length, 3);
  assert.equal(groupNotes.filter((group) => group.legend).length, 1);
  assert(
    groupNotes.every((group) =>
      group.legend ? group.notes.length === 0 : group.notes.length === 1,
    ),
  );
  const notes = groupNotes.flatMap((group) => group.notes);
  assert.equal(new Set(notes).size, notes.length);
  assert(notes.every((note) => !/подтвержден/iu.test(note)));
  for (const result of results) {
    const card = page.getByTestId(`hotel-card-${result.hotel.id}`);
    assert.equal(
      (
        await card
          .locator(".room-suggestion > summary > .eyebrow")
          .textContent()
      ).trim(),
      "Лучший вариант",
    );
  }
  await page.screenshot({
    path: resolve(artifacts, "1440-partial-groups.png"),
    fullPage: true,
  });

  results = [
    hit(0, "all", "yes", "Просите номер: западная сторона, 3-й этаж."),
  ];
  await page.reload();
  await page.locator(".result-card").first().waitFor();
  assert.equal(await page.getByTestId("partial-legend").count(), 0);
  assert.equal(
    (
      await page.locator(".room-suggestion > summary > .eyebrow").textContent()
    ).trim(),
    "Просите номер",
  );

  results = [
    hit(
      0,
      "unknown",
      "unknown",
      "Уточните сторону, этаж и доступность конкретного номера у отеля.",
    ),
  ];
  await page.reload();
  await page.locator(".result-card").first().waitFor();
  assert.equal(
    (
      await page.locator(".room-suggestion > summary > .eyebrow").textContent()
    ).trim(),
    "Лучший вариант",
  );
  assert.deepEqual(errors, []);
  assert.deepEqual(blocked, []);
  await context.close();
  const report = {
    passed: true,
    checks: [
      "one partial legend across three groups; no duplicate note in its group; other group notes distinct",
      "recommendation title follows backend full-match prefix for yes, partial, unknown and no",
    ],
    errors,
    blocked,
  };
  await writeFile(
    resolve(artifacts, "report.json"),
    JSON.stringify(report, null, 2),
  );
  console.log(JSON.stringify(report, null, 2));
  passed = true;
} finally {
  await Promise.race([
    browser.close(),
    new Promise((done) => setTimeout(done, 5000)),
  ]);
  if (passed) process.exit(0);
}
