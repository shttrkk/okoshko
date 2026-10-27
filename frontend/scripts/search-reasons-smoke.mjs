import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { mkdir, readFile } from "node:fs/promises";
import { chromium } from "playwright";

// API and hotel data are synthetic browser fixtures; no request reaches 8080.
const base = process.env.OKNO_BASE_URL || "http://localhost:5177";
const chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const executablePath =
  process.env.OKNO_BROWSER_EXECUTABLE ||
  (existsSync(chrome) ? chrome : undefined);
const artifacts = new URL(
  "../../runtime/night/search-reasons-smoke/",
  import.meta.url,
);
await mkdir(artifacts, { recursive: true });
const beach = {
  characteristic_id: "beach_walk",
  label: "До пляжа",
  field: "minutes",
  op: "lte",
  value: 8,
  weight: 1,
  scope: null,
  no_stairs: true,
  no_crossings: true,
};
const sea = {
  characteristic_id: "sea_view",
  label: "Море из окна",
  field: "available",
  op: "eq",
  value: true,
  weight: 1,
  scope: null,
};
const quiet = {
  characteristic_id: "quiet_night",
  label: "Тишина ночью",
  field: "available",
  op: "eq",
  value: true,
  weight: 1,
  scope: null,
};
const missing = {
  characteristic_id: "connectivity",
  label: "Интернет для работы",
  field: "download_mbps",
  op: "gte",
  value: 30,
  weight: 1,
  scope: null,
};
const parsed = {
  text: "Море из окна, до пляжа без лестниц, интернет обязателен; желательно тихо",
  must: [beach, sea],
  nice: [quiet],
  no_data: [missing],
  no_data_must: [missing],
  check_in: null,
  check_out: null,
  month: 9,
  facade: null,
  min_floor: null,
  unmapped: [],
  clarifications: [],
  mode: "offline",
};
const collection = { type: "FeatureCollection", features: [] };
const names = [
  "Лидер браузерной проверки",
  "Близкая альтернатива",
  "Следующая альтернатива",
  "Ещё один близкий вариант",
  "Дальше по списку",
  "Последний вариант",
];
const hotels = names.map((name, index) => ({
  id: `reason-fixture-${index}`,
  name,
  address: "Синтетическая browser fixture",
  lat: 43.58 + index * 0.0001,
  lon: 39.72,
  stars: null,
  price_rub: null,
  basic_fields_source: "fixture",
  source_ids: [],
  floors: null,
  footprint: null,
  image: null,
  image_attribution: null,
  website: null,
  room_types: [],
  tags: {},
}));
// Exact current backend relative_reason string forms, checked against the local
// Python function. They describe these fixture profiles, not real hotels.
const countReason = `«${names[0]}»: подтверждено 2 из 3 обязательных условий, здесь — 1.`;
const scoreReason = (reference, higher, current) =>
  `«${reference}»: выше совокупная оценка выполнения условий с учётом уверенности (${higher} против ${current}); проверьте оговорки в критериях.`;
const verdictReason = `«${names[1]}»: условие «Море из окна» лучше подтверждено; здесь ? — Для выбранного фасада нет достаточных данных`;
const tieReason = `«${names[2]}»: оценки совпадают; имеющиеся данные не дают основания считать этот отель лучше.`;
const initialReasons = [
  ["Первое место не должно получать блок объяснения более низкой позиции."],
  [countReason],
  [scoreReason(names[1], 71, 65), countReason],
  [scoreReason(names[2], 65, 60), scoreReason(names[1], 71, 60), countReason],
  [countReason],
  [countReason],
];
let mode = "normal";
const searchBodies = [],
  requests = [],
  errors = [],
  failures = [],
  checks = [];
function response(body) {
  const query = structuredClone(body.parsed || parsed);
  const results = hotels.map((hotel, index) => ({
    hotel,
    score: [85, 71, 65, 60, 55, 50][index],
    group: "almost",
    must_satisfied: index === 0 ? 2 : 1,
    must_total: query.must.length + query.no_data_must.length,
    summary:
      "Браузерная проверка: часть обязательных данных требует уточнения.",
    verdicts: [],
    room_recommendation: null,
    disappointments: [],
    why_not_higher: body.parsed ? [] : initialReasons[index],
  }));
  if (mode === "stress") {
    // The wire type is an array: exercise duplicate/overlong payloads without
    // claiming that the normal ranker emits more than three reasons.
    results[1].why_not_higher = [
      "",
      " ",
      countReason,
      countReason.replace(`«${names[0]}»`, `«${names[2]}»`),
      scoreReason(names[0], 85, 71),
      scoreReason(names[2], 80, 71),
      verdictReason,
      tieReason,
      "Дополнительная исходная строка за лимитом.",
    ];
    results[2].why_not_higher = [
      tieReason,
      tieReason.replace(`«${names[2]}»`, `«${names[0]}»`),
    ];
    results[3].why_not_higher = [
      "«Имя <b>как текст</b>»: оценки совпадают; имеющиеся данные не дают основания считать этот отель лучше.",
    ];
  }
  if (mode === "missing") {
    delete results[1].why_not_higher;
    results[2].why_not_higher = [];
    results[3].why_not_higher = ["", "  "];
  }
  if (mode === "regrouped") results[0].group = "unknown";
  return {
    parsed: query,
    results,
    total: results.length,
    latency_ms: 12,
    run_id: null,
    mode: "offline",
  };
}
const browser = await chromium.launch({ executablePath, headless: true });
try {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
  });
  await context.route("**/data/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith(".geojson"))
      return route.fulfill({ json: collection });
    if (url.pathname.endsWith(".json")) return route.fulfill({ json: {} });
    const bytes = await readFile(
      new URL(`../..${decodeURIComponent(url.pathname)}`, import.meta.url),
    );
    const range = route
      .request()
      .headers()
      .range?.match(/bytes=(\d+)-(\d*)/);
    if (range) {
      const first = Number(range[1]),
        last = Math.min(Number(range[2]) || bytes.length - 1, bytes.length - 1);
      return route.fulfill({
        status: 206,
        body: bytes.subarray(first, last + 1),
        headers: {
          "content-range": `bytes ${first}-${last}/${bytes.length}`,
          "accept-ranges": "bytes",
        },
      });
    }
    return route.fulfill({ body: bytes });
  });
  await context.route("**/api/**", async (route) => {
    if (new URL(route.request().url()).pathname.endsWith("/room-advice"))
      return route.fulfill({ json: [] });
    if (new URL(route.request().url()).pathname.endsWith("/rooms"))
      return route.fulfill({ json: [] });
    const request = route.request(),
      path = new URL(request.url()).pathname;
    requests.push({ path, method: request.method() });
    if (path === "/api/search") {
      const body = request.postDataJSON();
      searchBodies.push(body);
      return route.fulfill({ json: response(body) });
    }
    if (path === "/api/status")
      return route.fulfill({
        json: {
          name: "Browser fixture",
          mode: "offline",
          version: "fixture",
          hotel_count: hotels.length,
          characteristic_count: 4,
          seed_version: "fixture",
        },
      });
    if (path === "/api/hotels") return route.fulfill({ json: hotels });
    if (path.endsWith("/geo"))
      return route.fulfill({
        json: { route: collection, view_cones: collection, facades: [] },
      });
    if (path === "/api/characteristics") return route.fulfill({ json: [] });
    if (path === "/api/revision")
      return route.fulfill({
        json: { instance: "reasons-fixture", revision: 0 },
      });
    throw new Error(`Unexpected fixture API: ${request.method()} ${path}`);
  });
  const page = await context.newPage();
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("response", (res) => {
    if (res.url().startsWith(base) && res.status() >= 400)
      failures.push(`${res.status()} ${res.url()}`);
  });
  const load = async () => {
    await page.goto(`${base}/search?q=${encodeURIComponent(parsed.text)}`);
    await page.getByTestId(`hotel-card-${hotels.at(-1).id}`).waitFor();
    await page.getByTestId("default-month-chip").waitFor();
  };
  await load();
  assert.equal(
    await page.locator('[data-testid^="search-reasons-"]').count(),
    3,
  );
  assert.equal(
    await page.getByTestId(`search-reasons-${hotels[0].id}`).count(),
    0,
  );
  assert.equal(
    await page.getByTestId(`search-reasons-${hotels[4].id}`).count(),
    0,
  );
  assert.equal(
    await page.getByTestId(`search-reasons-${hotels[5].id}`).count(),
    0,
  );
  const firstAlternative = page.getByTestId(`search-reasons-${hotels[1].id}`);
  assert.equal(await firstAlternative.getAttribute("open"), null);
  assert.equal(await firstAlternative.locator("p").first().isVisible(), false);
  await firstAlternative.locator("summary").focus();
  await page.keyboard.press("Enter");
  assert.equal(await firstAlternative.getAttribute("open"), "");
  assert.deepEqual(await firstAlternative.locator("p").allTextContents(), [
    countReason,
  ]);
  const thirdAlternative = page.getByTestId(`search-reasons-${hotels[3].id}`);
  assert.deepEqual(await thirdAlternative.locator("p").allTextContents(), [
    scoreReason(names[2], 65, 60),
    countReason,
  ]);
  await page.screenshot({
    path: new URL("desktop.png", artifacts).pathname,
    fullPage: true,
  });
  checks.push(
    "only ranked places 2–4 receive collapsed details; native keyboard toggle works; strings remain exact backend output",
  );
  checks.push(
    "repeated generic score comparisons with different reference names/numbers appear once per block",
  );

  const criteria = page.getByRole("region", { name: "Условия поиска" });
  assert.match(
    await page.getByTestId("default-month-chip").innerText(),
    /Сентябрь.*по умолчанию/,
  );
  await page.getByTestId("missing-required-data").waitFor();
  assert.match(
    await criteria.innerText(),
    /обязательно · нет данных в регионе/,
  );
  await page.getByLabel("Порог: До пляжа", { exact: true }).fill("6");
  await page
    .getByRole("button", {
      name: "Переместить Тишина ночью в обязательные",
      exact: true,
    })
    .click();
  await page.getByTestId("default-month-chip").click();
  await page.getByLabel("Месяц поездки", { exact: true }).selectOption("6");
  await page
    .getByLabel("Уточнить дату заезда", { exact: true })
    .fill("2027-06-11");
  await page
    .getByLabel("Уточнить дату выезда", { exact: true })
    .fill("2027-06-16");
  const changed = page.waitForResponse(
    (res) =>
      res.url().endsWith("/api/search") &&
      !!res.request().postDataJSON()?.parsed,
  );
  await page.getByRole("button", { name: "Применить", exact: true }).click();
  await changed;
  const applied = searchBodies.at(-1).parsed;
  assert.equal(applied.month, 6);
  assert.equal(applied.check_in, "2027-06-11");
  assert.equal(applied.check_out, "2027-06-16");
  assert.deepEqual(applied.no_data_must, parsed.no_data_must);
  assert.deepEqual(applied.no_data, parsed.no_data);
  assert.deepEqual(
    applied.must.map((criterion) => criterion.characteristic_id),
    ["beach_walk", "sea_view", "quiet_night"],
  );
  assert.equal(applied.must[0].value, 6);
  assert.equal(applied.must[0].no_stairs, true);
  assert.equal(applied.must[0].no_crossings, true);
  await page.getByTestId("trip-dates-chip").waitFor();
  await page.waitForFunction(
    () => !document.querySelector('[data-testid^="search-reasons-"]'),
  );
  await page.getByTestId("missing-required-data").waitFor();
  checks.push(
    "query editor thresholds, must/nice moves, route flags, hard no_data, month chip and explicit dates survive applying edited parsed query",
  );

  mode = "stress";
  await load();
  await page
    .getByTestId(`search-reasons-${hotels[1].id}`)
    .locator("summary")
    .click();
  assert.deepEqual(
    await page
      .getByTestId(`search-reasons-${hotels[1].id}`)
      .locator("p")
      .allTextContents(),
    [countReason, scoreReason(names[0], 85, 71), verdictReason],
  );
  assert.deepEqual(
    await page
      .getByTestId(`search-reasons-${hotels[2].id}`)
      .locator("p")
      .allTextContents(),
    [tieReason],
  );
  assert.equal(
    await page
      .getByTestId(`search-reasons-${hotels[3].id}`)
      .locator("b")
      .count(),
    0,
  );
  assert.match(
    await page
      .getByTestId(`search-reasons-${hotels[3].id}`)
      .locator("p")
      .textContent(),
    /<b>как текст<\/b>/,
  );
  assert.ok(
    (
      await page.locator('[data-testid^="search-reasons-"] p').allTextContents()
    ).every((reason) => !reason.includes("за лимитом")),
  );
  checks.push(
    "duplicate bodies/ties and empty strings are removed, reasons cap at three, backend text is rendered as safe text",
  );
  await page.setViewportSize({ width: 390, height: 844 });
  const mobileReasons = page.getByTestId(`search-reasons-${hotels[1].id}`);
  await mobileReasons.scrollIntoViewIfNeeded();
  assert.equal(
    await page.evaluate(() => document.documentElement.scrollWidth),
    390,
  );
  await mobileReasons.screenshot({
    path: new URL("mobile-390.png", artifacts).pathname,
  });
  checks.push(
    "expanded details fit mobile width 390 without horizontal overflow",
  );
  mode = "regrouped";
  await load();
  assert.equal(
    await page
      .locator(".result-card")
      .first()
      .getByTestId(`search-reasons-${hotels[1].id}`)
      .count(),
    0,
  );
  assert.equal(
    await page.locator('[data-testid^="search-reasons-"]').count(),
    2,
  );
  checks.push(
    "group ordering also keeps the first visible result free of explanation clutter",
  );
  mode = "missing";
  await load();
  assert.equal(
    await page.locator('[data-testid^="search-reasons-"]').count(),
    0,
  );
  checks.push(
    "absent, empty and whitespace-only backend reasons produce no invented fallback or empty heading",
  );
  assert.deepEqual(errors, []);
  assert.deepEqual(failures, []);
  assert.ok(
    requests.every(
      (request) => request.method === "GET" || request.path === "/api/search",
    ),
  );
  console.log(
    JSON.stringify(
      {
        fixture_only: true,
        browser: executablePath,
        checks,
        errors,
        failures,
        api_requests: requests.length,
        mocked_search_posts: searchBodies.length,
        artifacts: artifacts.pathname,
      },
      null,
      2,
    ),
  );
  await context.close();
} finally {
  await browser.close();
}
