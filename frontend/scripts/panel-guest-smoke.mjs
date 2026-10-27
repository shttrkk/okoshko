import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

// Run against the rebuilt application; search and evidence use its real API. All question/passport mutations below are intercepted fixtures.
const base = process.env.OKNO_BASE_URL || "http://localhost:8080";
const apiBase = process.env.OKNO_API_BASE_URL || "http://localhost:8080";
const artifacts = resolve(
  process.env.OKNO_PANEL_GUEST_ARTIFACTS ||
    fileURLToPath(new URL("../../runtime/panel-guest-after", import.meta.url)),
);
await mkdir(artifacts, { recursive: true });
const browser = await chromium.launch({
  headless: true,
  executablePath:
    process.env.OKNO_BROWSER_EXECUTABLE ||
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
});
const banned =
  /СИМУЛЯЦИЯ|Синтетический ответ|hotelier_simulator|synthetic-demo|фикстур|demo truth|Вымышленный ответ/i;
let smokePassed = false;
const errors = [],
  checks = [],
  blocked = [];
const api = async (path, body) => {
  const response = await fetch(
    `${apiBase}${path}`,
    body
      ? {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }
      : undefined,
  );
  assert(response.ok, `${path}: ${response.status}`);
  return response.json();
};
const seed = await api("/api/search", {
  text: "тихий номер с видом на закат недалеко от моря",
  check_in: "2027-09-14",
  check_out: "2027-09-21",
  limit: 40,
});
assert(seed.results.length > 1);
const first = seed.results[0],
  hotelId = first.hotel.id;
const detail = await api(`/api/hotels/${encodeURIComponent(hotelId)}`);
async function context(viewport, fixture = false, colorScheme = "light") {
  const ctx = await browser.newContext({ viewport, colorScheme });
  const page = await ctx.newPage();
  page.on("pageerror", (error) => errors.push(error.message));
  // Install before any page action; never navigate to providers automatically.
  await page.route(/https?:\/\/([^/]*\.)?yandex\.[^/]+\//i, (route) => {
    blocked.push(route.request().url());
    return route.abort();
  });
  await page.route("**/api/**", async (route) => {
    const request = route.request(),
      path = new URL(request.url()).pathname;
    if (fixture && path === "/api/questions")
      return route.fulfill({
        json: {
          id: "panel-guest-question",
          hotel_id: hotelId,
          characteristic_id: "soundproofing",
          status: "pending",
        },
      });
    if (
      request.method() !== "GET" &&
      !["/api/search", "/api/compare"].includes(path) &&
      !/\/evaluate$/.test(path)
    ) {
      blocked.push(`${request.method()} ${path}`);
      return route.abort();
    }
    return route.continue();
  });
  return { ctx, page };
}
const screenshot = (page, name) =>
  page.screenshot({ path: resolve(artifacts, `${name}.png`), fullPage: true });
async function cleanVisible(page) {
  assert(
    !banned.test(await page.locator("body").innerText()),
    "Guest copy leaked technical demo wording",
  );
}
try {
  const { ctx, page } = await context({ width: 390, height: 844 });
  await page.goto(base);
  await page.getByTestId("search-input").waitFor();
  assert.equal(await page.getByTestId("search-input").inputValue(), "");
  await page.getByTestId("search-input").fill("Мой текст");
  await page.getByTestId("group-search-example").click();
  assert(
    (await page.getByTestId("search-input").inputValue()).startsWith(
      "Мой текст\n\nЕдем компанией:",
    ),
  );
  await page.getByTestId("check-in").fill("2027-09-14");
  await page.getByTestId("check-out").fill("2027-09-21");
  await screenshot(page, "390-home");
  await page.getByTestId("masha-example").click();
  await page.locator(".result-card").first().waitFor();
  assert.equal(new URL(page.url()).searchParams.get("check_in"), "2027-09-14");
  assert.equal(new URL(page.url()).searchParams.get("check_out"), "2027-09-21");
  assert.equal(await page.locator(".result-card").count(), seed.results.length);
  assert.match(
    await page.locator(".results-footnote").innerText(),
    /Показаны 40 лучших из \d+/,
  );
  assert(
    !/подтверждено|подтверждены/i.test(
      await page.locator(".required-status-list").first().innerText(),
    ),
  );
  await cleanVisible(page);
  await screenshot(page, "390-search");
  await page.goto(`${base}/booking/${encodeURIComponent(hotelId)}`);
  await page.getByTestId("passport-guest").waitFor();
  assert.equal(await page.getByTestId("passport-guest").inputValue(), "");
  assert.equal(
    await page.getByLabel("Заезд в паспорте").inputValue(),
    "2027-09-14",
  );
  assert.equal(await page.getByTestId("passport-preliminary").count(), 0);
  assert(await page.getByTestId("passport-recommended-scope").count());
  const labels = await page
    .getByLabel(/^Ожидание \d+$/)
    .evaluateAll((elements) => elements.map((element) => element.value));
  assert.equal(new Set(labels).size, labels.length);
  await screenshot(page, "390-passport");
  await page.goto(base);
  await page
    .getByRole("button", { name: /Тихий номер, чтобы выспаться/ })
    .click();
  await page.locator(".result-card").first().waitFor();
  assert.equal(new URL(page.url()).searchParams.get("check_in"), "2027-09-14");
  assert(
    !/плотн.*штор/i.test(await page.locator(".criteria-panel").innerText()),
  );
  checks.push(
    "390 light: empty input, append company, Masha one click, preset dates, no duplicate passport items or false no-date status",
  );
  await ctx.close();

  const desktop = await context({ width: 1440, height: 1000 });
  await desktop.page.goto(
    `${base}/search?q=${encodeURIComponent("до пляжа не больше 10 минут, вид на море")}`,
  );
  await desktop.page.locator(".result-card").first().waitFor();
  const threshold = desktop.page
    .getByRole("spinbutton", { name: /Порог:.*пляж/i })
    .first();
  await threshold.fill("7");
  await desktop.page
    .getByRole("button", { name: /^Применить/ })
    .first()
    .click();
  await desktop.page.waitForFunction(() =>
    [...document.querySelectorAll(".verdict-text strong")].some((el) =>
      /≤ 7/.test(el.textContent),
    ),
  );
  assert(
    !/≤ 10.*≤ 7/.test(
      await desktop.page.locator(".criteria-panel").innerText(),
    ),
  );
  await screenshot(desktop.page, "1440-threshold");
  await desktop.page.goto(
    `${base}/compare?ids=${seed.results
      .slice(0, 3)
      .map((hit) => hit.hotel.id)
      .join(",")}`,
  );
  await desktop.page.locator(".comparison-table tbody tr").first().waitFor();
  await cleanVisible(desktop.page);
  await screenshot(desktop.page, "1440-compare");
  checks.push(
    "1440 light: edited threshold matches verdict label; comparison renders without technical demo wording",
  );
  await desktop.ctx.close();

  const fixture = await context({ width: 1440, height: 1000 }, true);
  const variants = [
    {
      characteristic_id: "sea_view",
      label: "Вид на море",
      status: "yes",
      text: "По расчёту море видно с 3-го этажа",
      synthetic: false,
      hotelier_confirmed: false,
    },
    {
      characteristic_id: "room_orientation",
      label: "Сторона и этаж",
      status: "yes",
      text: "Отель указал номера 301–310",
      synthetic: false,
      hotelier_confirmed: true,
    },
    {
      characteristic_id: "pets",
      label: "С питомцем",
      status: "yes",
      text: "Питомцы до 15 кг",
      synthetic: true,
      hotelier_confirmed: false,
    },
    {
      characteristic_id: "quiet_night",
      label: "Тишина ночью",
      status: "partial",
      text: "Уличный шум около 45 дБ",
      synthetic: false,
      hotelier_confirmed: false,
    },
    {
      characteristic_id: "soundproofing",
      label: "Шумоизоляция и окна",
      status: "unknown",
      text: "Отель пока не ответил",
      synthetic: false,
      hotelier_confirmed: false,
    },
  ].map((v) => ({
    ...v,
    required: true,
    symbol: v.status === "yes" ? "✓" : v.status === "partial" ? "≈" : "?",
    caveat: null,
    disputed: false,
    fact_ids: ["panel-guest-fact"],
  }));
  const fact = {
    ...detail.facts[0],
    id: "panel-guest-fact",
    hotel_id: hotelId,
    characteristic_id: "pets",
    status: "computed",
    synthetic: true,
    confidence: 0.85,
    scope: { type: "floor_range", facade: "W", floor_min: 3, floor_max: 5 },
    supporting_claim_ids: [],
    conflicting_claim_ids: [],
    value: { available: true, max_weight_kg: 15 },
    evidence: [
      {
        id: "panel-evidence",
        kind: "simulation",
        label: "Синтетический ответ для демонстрации",
        source_ids: ["hotelier_simulator"],
        quote:
          "Синтетический ответ для демонстрации: Принимаем собак до 15 кг.",
        limitations: [
          "Вымышленный ответ для проверки synthetic-demo сценария",
          "Confidence относится к синтетической фикстуре",
        ],
        inputs: {
          raw_text:
            "Синтетический ответ для демонстрации: Принимаем собак до 15 кг.",
        },
        output: { available: true },
      },
    ],
  };
  const fixtureHit = {
    ...first,
    verdicts: variants,
    room_plan: [],
    must_total: 5,
    must_matched: 3,
  };
  await fixture.page.route("**/api/search", (route) =>
    route.fulfill({ json: { ...seed, results: [fixtureHit], total: 1 } }),
  );
  await fixture.page.route(
    `**/api/hotels/${hotelId}/facts/panel-guest-fact/evidence`,
    (route) => route.fulfill({ json: { fact, claims: [] } }),
  );
  await fixture.page.route(`**/api/hotels/${hotelId}/evaluate?*`, (route) =>
    route.fulfill({
      json: { ...detail, facts: [fact], verdicts: variants, claims: [] },
    }),
  );
  await fixture.page.goto(
    `${base}/search?q=${encodeURIComponent("с питомцем, вид на море")}`,
  );
  await fixture.page.locator(".result-card").first().waitFor();
  assert.equal(
    await fixture.page.getByTestId("demo-results-banner").count(),
    1,
  );
  const verdicts = fixture.page.locator(".verdict-list > li");
  assert.equal(
    (await verdicts.nth(0).locator(".verdict-symbol").innerText()).trim(),
    "≈",
  );
  assert.match(await verdicts.nth(0).innerText(), /по расчёту/);
  assert.equal(
    (await verdicts.nth(1).locator(".verdict-symbol").innerText()).trim(),
    "✓",
  );
  assert.match(await verdicts.nth(1).innerText(), /Подтвердил отель/);
  assert.equal(
    (await verdicts.nth(2).locator(".verdict-symbol").innerText()).trim(),
    "≈",
  );
  assert.match(await verdicts.nth(2).innerText(), /Ответ отеля · демо/);
  assert(!(await verdicts.nth(2).locator(".verdict-symbol.yes").count()));
  await screenshot(fixture.page, "1440-status-fixture");
  await verdicts.nth(2).getByRole("button").first().click();
  await fixture.page.getByRole("dialog").waitFor();
  await fixture.page
    .getByRole("dialog")
    .locator("details > summary")
    .first()
    .click();
  await fixture.page
    .getByRole("dialog")
    .getByText("Данные для разработчиков", { exact: true })
    .click();
  await cleanVisible(fixture.page);
  assert(
    !/85%|надёжно/.test(await fixture.page.getByRole("dialog").innerText()),
  );
  assert.match(
    await fixture.page.getByRole("dialog").innerText(),
    /Принимаем собак до 15 кг/,
  );
  assert.match(await fixture.page.getByRole("dialog").innerText(), /этажи 3–5/);
  await screenshot(fixture.page, "1440-source-fixture");
  await fixture.page
    .getByRole("button", { name: "Закрыть", exact: true })
    .click();
  let sent = 0;
  fixture.page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/api/questions") sent++;
  });
  await fixture.page.getByTestId("ask-soundproofing").click();
  assert.equal(sent, 0);
  await fixture.page
    .getByRole("dialog")
    .getByText(first.hotel.name, { exact: true })
    .waitFor();
  assert.match(
    await fixture.page.getByRole("dialog").innerText(),
    /здесь.*паспорте/,
  );
  await fixture.page
    .getByRole("button", { name: "Отмена", exact: true })
    .click();
  assert.equal(sent, 0);
  const offlineQuestion = (route) => route.abort();
  await fixture.page.route("**/api/questions", offlineQuestion);
  await fixture.page.getByTestId("ask-soundproofing").click();
  await fixture.page.getByTestId("ask-confirm").click();
  await fixture.page
    .getByRole("alert")
    .getByText(/Не удалось связаться с сервисом/)
    .waitFor();
  assert(
    !/Failed to fetch/.test(await fixture.page.getByRole("dialog").innerText()),
  );
  await fixture.page
    .getByRole("button", { name: "Отмена", exact: true })
    .click();
  await fixture.page.unroute("**/api/questions", offlineQuestion);
  await fixture.page.getByTestId("ask-soundproofing").click();
  await fixture.page.getByTestId("ask-confirm").click();
  await fixture.page
    .getByTestId("ask-soundproofing")
    .getByText("Вопрос отправлен")
    .waitFor();
  assert.equal(sent, 2);
  await fixture.page.reload();
  await fixture.page
    .getByTestId("ask-soundproofing")
    .getByText("Вопрос отправлен")
    .waitFor();
  assert(await fixture.page.getByTestId("ask-soundproofing").isDisabled());
  const saved = await fixture.page.evaluate(() =>
    Object.entries(localStorage).filter(([key]) =>
      key.startsWith("okno-question:"),
    ),
  );
  assert.equal(saved.length, 1);
  assert.equal(saved[0][1], "panel-guest-question");
  checks.push(
    "fixtures: real hotel answer only gets check; demo separate; confidence hidden; legacy metadata stripped, quote content preserved; Ask cancel/send/reload without stand writes",
  );
  fixtureHit.verdicts = [
    {
      ...variants[0],
      synthetic: true,
      basis: "calculation",
      text: "По расчёту с новым домом виден край моря",
    },
  ];
  fixtureHit.must_total = 1;
  fixtureHit.must_matched = 1;
  fixtureHit.room_profile = null;
  fact.characteristic_id = "sea_view";
  fact.value = { available: true, min_floor: 3, probability: 0.8 };
  fact.evidence = [
    {
      id: "panel-geometry",
      kind: "calculation",
      label: "Расчёт с новым зданием",
      source_ids: ["geo_engine"],
      quote: null,
      limitations: ["Показан пример пересчёта"],
      inputs: {},
      output: { probability: 0.8 },
    },
  ];
  await fixture.page.reload();
  await fixture.page.locator(".result-card").first().waitFor();
  assert.equal(
    await fixture.page.getByTestId("demo-results-banner").count(),
    0,
  );
  assert.match(
    await fixture.page.locator(".verdict-list").innerText(),
    /Демо-расчёт/,
  );
  assert(
    !/ответ отеля/i.test(
      await fixture.page.locator(".verdict-list").innerText(),
    ),
  );
  await fixture.page.locator(".verdict-text").first().click();
  await fixture.page.getByRole("dialog").waitFor();
  await fixture.page.getByRole("dialog").locator(".evidence-card").first().waitFor();
  assert.match(
    await fixture.page.getByRole("dialog").innerText(),
    /Демо-расчёт/,
  );
  assert(
    !/ответ отеля|80%|85%/i.test(
      await fixture.page.getByRole("dialog").innerText(),
    ),
  );
  await fixture.page
    .getByRole("dialog")
    .getByText("Для дотошных", { exact: true })
    .click();
  assert.match(
    await fixture.page.getByRole("dialog").innerText(),
    /Вероятность видимости по модели: 80%/,
  );
  await cleanVisible(fixture.page);
  await screenshot(fixture.page, "1440-geometry-demo-fixture");
  checks.push(
    "geometry fixture: synthetic calculation is not attributed to a hotel; model probability retained only under details",
  );
  await fixture.ctx.close();
  const dark = await context({ width: 1280, height: 800 }, false, "dark");
  await dark.page.goto(base);
  await dark.page.locator("[data-theme=dark]").waitFor();
  await dark.page
    .getByRole("button", { name: "Включить светлую тему", exact: true })
    .click();
  await dark.page.reload();
  await dark.page.locator("[data-theme=light]").waitFor();
  checks.push(
    "system theme: dark preference is used initially; an explicit light selection survives reload",
  );
  await dark.ctx.close();
  assert.deepEqual(errors, []);
  const report = { passed: true, checks, blocked, errors, artifacts };
  await writeFile(
    resolve(artifacts, "report.json"),
    JSON.stringify(report, null, 2),
  );
  console.log(JSON.stringify(report, null, 2));
  smokePassed = true;
} finally {
  // All contexts are closed above. System Chrome can retain its driver pipe on
  // macOS after a successful close, so keep teardown bounded for this CLI.
  await Promise.race([
    browser.close(),
    new Promise((done) => setTimeout(done, 5000)),
  ]);
  if (smokePassed) process.exit(0);
}
