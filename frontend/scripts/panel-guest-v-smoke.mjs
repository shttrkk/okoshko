import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import {
  queryFixture,
  suggestionCriterion,
} from "./panel-guest-v-fixtures.mjs";
const base = process.env.OKNO_BASE_URL || "http://localhost:8080";
const apiBase = process.env.OKNO_API_BASE_URL || base;
const artifacts = resolve(
  process.env.OKNO_PANEL_GUEST_ARTIFACTS ||
    fileURLToPath(new URL("../../runtime/panel-guest-v", import.meta.url)),
);
await mkdir(artifacts, { recursive: true });
const readSearch = async (text) => {
  const response = await fetch(`${apiBase}/api/search`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, limit: 40 }),
  });
  assert(response.ok);
  return response.json();
};
const seed = await readSearch("тихий номер с видом на закат недалеко от моря");
assert(seed.results.length >= 3);
const browser = await chromium.launch({
  headless: true,
  executablePath:
    process.env.OKNO_BROWSER_EXECUTABLE ||
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
});
const report = {
  passed: false,
  real: [],
  fixtures: [],
  cases: [],
  errors: [],
  blocked: [],
};
let passed = false;
async function surface(viewport, colorScheme = "light", fixture = false) {
  const context = await browser.newContext({ viewport, colorScheme });
  const page = await context.newPage();
  page.on("pageerror", (error) => report.errors.push(error.message));
  await page.route(/yandex\.|ya\.ru|mapillary/i, (route) => {
    report.blocked.push(route.request().url());
    return route.abort();
  });
  await page.route("**/api/**", (route) => {
    const request = route.request(),
      path = new URL(request.url()).pathname;
    if (fixture && path === "/api/questions")
      return route.fulfill({
        json: {
          id: "panel-v-question",
          hotel_id: seed.results[0].hotel.id,
          characteristic_id: "soundproofing",
          status: "pending",
        },
      });
    if (
      request.method() !== "GET" &&
      !["/api/search", "/api/compare"].includes(path) &&
      !path.endsWith("/evaluate")
    ) {
      report.blocked.push(`${request.method()} ${path}`);
      return route.abort();
    }
    return route.continue();
  });
  return { context, page };
}
const capture = (page, name, fullPage = false) =>
  page.screenshot({ path: resolve(artifacts, `${name}.png`), fullPage });
const storedQuery = (page) =>
  page.evaluate(() => JSON.parse(sessionStorage.getItem("okno-parsed")));
const forbidden =
  /Copernicus|Monte Carlo|geo_engine|СИМУЛЯЦИЯ|hotelier_simulator|synthetic-demo|demo truth|OSM osm-/i;
try {
  const cases = [
    {
      name: "olga",
      viewport: { width: 390, height: 844 },
      scheme: "light",
      q: "тихий номер с видом на закат недалеко от моря",
      check_in: "2027-09-14",
      check_out: "2027-09-21",
    },
    {
      name: "igor",
      viewport: { width: 1440, height: 1000 },
      scheme: "light",
      q: "Едем компанией: тёще — тихий номер, окна во двор, не выше 3 этажа; нам — вид на море. Для всех — до пляжа не больше 10 минут, без лестниц, с коляской",
      check_in: "2027-09-06",
      check_out: "2027-09-16",
    },
    {
      name: "dina",
      viewport: { width: 1280, height: 800 },
      scheme: "dark",
      q: "с собакой 15 кг, кафе и бары рядом, быстрый интернет не меньше 30 Мбит, центр Сочи",
      check_in: "2027-09-12",
      check_out: "2027-09-19",
    },
  ];
  for (const item of cases) {
    const { context, page } = await surface(item.viewport, item.scheme);
    const similar = [];
    page.on("request", (request) => {
      if (
        new URL(request.url()).pathname === "/api/search" &&
        request.method() === "POST"
      ) {
        const body = request.postDataJSON();
        if (body.similar_to) similar.push(body);
      }
    });
    await page.goto(base);
    assert.equal(await page.getByTestId("search-input").inputValue(), "");
    if (item.name === "dina") {
      await page.locator("[data-theme=dark]").waitFor();
      await page.getByTestId("search-input").fill("с собакой 15 кг");
      await page.getByTestId("group-search-example").click();
      assert.equal(
        await page.getByTestId("search-input").inputValue(),
        "с собакой 15 кг",
      );
    }
    const params = new URLSearchParams({
      q: item.q,
      check_in: item.check_in,
      check_out: item.check_out,
    });
    await page.goto(`${base}/search?${params}`);
    const card = page.locator(".result-card").first();
    await card.waitFor();
    assert(!forbidden.test(await card.innerText()));
    assert((await card.getByTestId("compact-number-phrase").count()) <= 1);
    assert(!/%|≈ (\d+) мин вместо ≈ \1 мин/.test(await card.innerText()));
    const state = await storedQuery(page);
    const initialState = JSON.stringify(state);
    const suggestions = await page.getByTestId("room-suggestion").count();
    if (item.name === "dina") assert.equal(suggestions, 0);
    if (item.name === "olga") {
      assert(suggestions > 0);
      assert.match(
        await card.getByTestId("room-suggestion").innerText(),
        /Потому что:/,
      );
      const details = card.getByTestId("card-details");
      await details.locator(":scope > summary").click();
      assert((await details.locator(":scope > p").count()) <= 2);
      assert.equal(
        await details.getByTestId("card-proof").getAttribute("open"),
        null,
      );
      assert(!forbidden.test(await details.innerText()));
      await details.locator(":scope > summary").click();
    }
    if (item.name === "igor") {
      assert(await card.getByTestId("group-plan").count());
      assert.match(await card.getByTestId("group-plan").innerText(), /Тёще/);
      assert.match(await card.getByTestId("group-plan").innerText(), /Нам/);
    }
    await card.screenshot({
      path: resolve(artifacts, `${item.name}-card.png`),
    });
    if (item.name === "olga") {
      await card.locator(".verdict-text").first().click();
      await page.getByRole("dialog").waitFor();
      assert(!forbidden.test(await page.getByRole("dialog").innerText()));
      assert.equal(
        await page.getByRole("dialog").locator(".metadata:visible").count(),
        0,
      );
      await capture(page, "olga-proof-human");
      await page
        .getByRole("dialog")
        .getByText("Для дотошных", { exact: true })
        .first()
        .click();
      assert(
        await page.getByRole("dialog").locator(".metadata:visible").count(),
      );
      await capture(page, "olga-proof-technical");
      await page.getByRole("button", { name: "Закрыть", exact: true }).click();
    }
    await card.locator(".hotel-name").click();
    await page.locator(".hotel-fit").waitFor();
    await page.getByTestId("requested-facts").waitFor();
    const criteria = [
      ...state.must,
      ...state.nice,
      ...(state.no_data || []),
      ...(state.no_data_must || []),
      ...(state.rooms || []).flatMap((room) => [
        ...room.must,
        ...room.nice,
        ...(room.no_data || []),
        ...(room.no_data_must || []),
      ]),
    ];
    const wanted = new Set(
      criteria.map((criterion) => criterion.characteristic_id),
    );
    const shown = await page
      .locator(".requested-facts > .facts-panel [data-characteristic-id]")
      .evaluateAll((els) => els.map((el) => el.dataset.characteristicId));
    assert(shown.every((id) => wanted.has(id)));
    assert.equal(
      await page.getByTestId("other-hotel-facts").getAttribute("open"),
      null,
    );
    if (item.name === "dina") {
      assert.doesNotMatch(
        await page.locator("body").innerText(),
        /если едете с коляской/,
      );
      assert.equal(
        await page.getByTestId("hotel-room-profiles").getAttribute("open"),
        null,
      );
      assert.equal(
        await page.getByTestId("optional-room-tool").getAttribute("open"),
        null,
      );
    }
    await page.waitForFunction(
      () =>
        !document.querySelector(
          '[data-testid="related-hotels"] [role="status"]',
        ),
    );
    assert(similar.length > 0);
    assert.deepEqual(similar.at(-1).parsed.must, state.must);
    assert.equal(similar.at(-1).check_in, item.check_in);
    assert.equal(similar.at(-1).check_out, item.check_out);
    assert.equal(JSON.stringify(await storedQuery(page)), initialState);
    assert.equal(
      await page.locator('a[href^="/"] .lucide-arrow-up-right').count(),
      0,
    );
    assert.match(
      await page.locator("body").innerText(),
      /OpenStreetMap contributors/,
    );
    await capture(page, `${item.name}-hotel`, true);
    report.cases.push({
      name: item.name,
      theme: await page.locator("[data-theme]").getAttribute("data-theme"),
      room_suggestions: suggestions,
      hotel_height: await page.evaluate(
        () => document.documentElement.scrollHeight,
      ),
      primary_fact_ids: shown,
    });
    report.real.push(
      `${item.name}: requested conditions first, isolated similarity payload with dates, original AppState preserved`,
    );
    await context.close();
    console.log(`PASS real ${item.name}`);
  }

  const { context, page } = await surface(
    { width: 1440, height: 1000 },
    "light",
    true,
  );
  let mode = "choice-global",
    posts = [],
    questionPosts = 0;
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/api/questions") questionPosts++;
  });
  await page.route("**/api/search", (route) => {
    const body = route.request().postDataJSON();
    posts.push(body);
    return route.fulfill({ json: queryFixture(seed, mode, body.parsed) });
  });
  await page.goto(`${base}/search?q=choice-global`);
  await page.getByTestId("query-suggestion").waitFor();
  const before = posts.length;
  assert.match(
    await page.locator(".criteria-panel").innerText(),
    /недалеко, центр/,
  );
  await page.getByTestId("query-suggestion").click();
  assert.equal(posts.length, before);
  assert.match(await page.locator(".criteria-panel").innerText(), /центр/);
  assert(
    !/Не удалось превратить в условие: недалеко/.test(
      await page.locator(".criteria-panel").innerText(),
    ),
  );
  await page
    .getByRole("button", { name: /^Применить/ })
    .first()
    .click();
  await page.waitForFunction(
    () => JSON.parse(sessionStorage.getItem("okno-parsed")).must.length === 1,
  );
  assert.deepEqual(posts.at(-1).parsed.must[0], suggestionCriterion);
  assert.equal(posts.at(-1).parsed.check_in, "2027-09-14");
  assert.deepEqual(posts.at(-1).parsed.unmapped, ["центр"]);
  report.fixtures.push(
    "structured global quick-add: no request before Apply, exact criterion/dates retained, ambiguous centre stays unknown",
  );
  mode = "choice-room";
  posts = [];
  await page.goto(`${base}/search?q=choice-room`);
  await page.getByTestId("query-suggestion").waitFor();
  const roomBefore = posts.length;
  await page
    .getByTestId("room-criteria-0")
    .getByTestId("query-suggestion")
    .click();
  assert.equal(posts.length, roomBefore);
  assert.match(await page.getByTestId("room-criteria-0").innerText(), /двор/);
  assert.match(
    await page.getByTestId("room-criteria-1").innerText(),
    /необычный интерьер/,
  );
  await page
    .getByRole("button", { name: /^Применить/ })
    .last()
    .click();
  await page.waitForFunction(
    () =>
      JSON.parse(sessionStorage.getItem("okno-parsed")).rooms?.[0].must
        .length === 1,
  );
  const grouped = posts.at(-1).parsed;
  assert.equal(grouped.must.length, 0);
  assert.equal(grouped.rooms[1].must.length, 0);
  assert.equal(grouped.rooms[0].must[0].scope.floor_max, 5);
  assert.deepEqual(grouped.rooms[0].unmapped, ["двор"]);
  report.fixtures.push(
    "room quick-add keeps its recipient and scope, and leaves other unmapped wishes intact",
  );
  mode = "known-statuses";
  await page.goto(`${base}/search?q=known-statuses`);
  await page.locator(".result-card").first().waitFor();
  const cards = page.locator(".result-card");
  assert.match(await cards.nth(0).innerText(), /не подходит.*|Не подходит/);
  assert.match(await cards.nth(0).innerText(), /37 м/);
  assert.match(await cards.nth(1).innerText(), /9 заведений из 30/);
  assert.match(
    await cards.nth(2).innerText(),
    /Wi-Fi указан, скорость не измерена/,
  );
  assert.equal(
    (
      await cards.nth(2).locator(".verdict-symbol").first().textContent()
    ).trim(),
    "≈",
  );
  for (const [index, unrelated] of [
    [0, /наличие номеров|окон на фасаде|это вопрос отельеру/i],
    [1, /больниц/i],
  ]) {
    const details = cards.nth(index).getByTestId("card-details");
    await details.locator(":scope > summary").click();
    await details.getByTestId("card-proof").locator(":scope > summary").click();
    assert(!unrelated.test(await details.innerText()));
  }
  report.fixtures.push(
    "route and nightlife proof do not mix in window or hospital caveats; original fixture/API caveats are untouched",
  );
  await page.getByTestId("ask-soundproofing").click();
  assert.equal(questionPosts, 0);
  await page.getByRole("button", { name: "Отмена", exact: true }).click();
  assert.equal(questionPosts, 0);
  await page.getByTestId("ask-soundproofing").click();
  await page.getByTestId("ask-confirm").click();
  await page
    .getByTestId("ask-soundproofing")
    .getByText("Вопрос отправлен")
    .waitFor();
  assert.equal(questionPosts, 1);
  await capture(page, "known-statuses-fixture", true);
  report.fixtures.push(
    "pending backend integration: known stairs/threshold fail render as no; reported Wi-Fi as partial; Ask confirmation uses intercepted POST",
  );
  await page.goto(base);
  await page
    .getByRole("button", { name: "Включить тёмную тему", exact: true })
    .click();
  await page.locator("[data-theme=dark]").waitFor();
  await page.evaluate(() => {
    sessionStorage.setItem("okno-token", "panel-v-local-session");
    sessionStorage.setItem(
      "okno-hotelier-session",
      JSON.stringify({ role: "demo_admin", hotel_ids: [] }),
    );
    window.dispatchEvent(new Event("okno-auth-changed"));
  });
  await page.getByTestId("header-logout").waitFor();
  let rejectLogout = true;
  await page.route("**/api/hotelier/logout", (route) =>
    rejectLogout
      ? route.abort()
      : route.fulfill({ json: { logged_out: true, synthetic: true } }),
  );
  await page.getByTestId("header-logout").click();
  await page
    .getByRole("status")
    .getByText(/Не удалось выйти/)
    .waitFor();
  assert.equal(
    await page.evaluate(() => sessionStorage.getItem("okno-token")),
    "panel-v-local-session",
  );
  rejectLogout = false;
  await page.getByTestId("header-logout").click();
  await page.waitForFunction(() => !sessionStorage.getItem("okno-token"));
  await page.getByTestId("header-logout").waitFor({ state: "hidden" });
  assert.equal(await page.getByTestId("header-logout").count(), 0);
  assert.equal(
    await page.evaluate(() => localStorage.getItem("okno-theme")),
    "dark",
  );
  await page.getByRole("button", { name: "Для команды", exact: false }).click();
  assert.equal(
    await page.locator(".nav-dropdown .lucide-arrow-up-right").count(),
    0,
  );
  report.fixtures.push(
    "global team menu and logout: failure retains session, successful logout emits state change and preserves explicit theme",
  );
  await context.close();
  assert.deepEqual(report.errors, []);
  assert.deepEqual(report.blocked, []);
  report.passed = true;
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
