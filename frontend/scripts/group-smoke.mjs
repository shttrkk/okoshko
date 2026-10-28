import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { mkdir, writeFile } from "node:fs/promises";
import { chromium } from "playwright";
import {
  closeSmokeBrowser,
  blockYandex,
  revealAncestors,
} from "./panel-guest-smoke-ui.mjs";

// No state-changing requests. Default: real API. Explicit fixture mode supports
// UI development before the group endpoints are deployed.
const base = process.env.OKNO_BASE_URL;
assert(base, "Set OKNO_BASE_URL to the frontend serving the real group API");
const fixture = process.env.OKNO_GROUP_FIXTURE === "1";
const chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const browser = await chromium.launch({
  headless: true,
  executablePath:
    process.env.OKNO_BROWSER_EXECUTABLE ||
    (existsSync(chrome) ? chrome : undefined),
});
const artifacts = new URL("../../runtime/group/", import.meta.url);
await mkdir(artifacts, { recursive: true });
const c = (characteristic_id, label, field = "available", value = true) => ({
  characteristic_id,
  label,
  field,
  value,
  op: "eq",
  weight: 1,
  scope: null,
});
const unavailable = c("room_orientation", "Окна во двор", "courtyard");
const parsed = {
  text: "Едем компанией: Маше тихий номер, Игорю вид на море. Для всех пляж рядом",
  must: [{ ...c("beach_walk", "До пляжа", "minutes", 15), op: "lte" }],
  nice: [],
  check_in: "2027-09-12",
  check_out: "2027-09-18",
  month: 9,
  facade: null,
  min_floor: null,
  unmapped: [],
  clarifications: [],
  mode: "offline",
  no_data: [],
  no_data_must: [],
  rooms: [
    {
      label: "Маша",
      must: [c("quiet_night", "Тишина ночью", "index", "quiet")],
      nice: [c("sunset_view", "Закат из окна")],
      scope: { facade: "W", floor_min: 1, floor_max: 3 },
      no_data: [unavailable],
      no_data_must: [unavailable],
      unmapped: ["подальше от лифта"],
      clarifications: [],
    },
    {
      label: "Игорь",
      must: [c("sea_view", "Вид на море")],
      nice: [],
      scope: { facade: "E", floor_min: 1, floor_max: 2 },
      no_data: [],
      no_data_must: [],
      unmapped: [],
      clarifications: [],
    },
  ],
};
const errors = [],
  failures = [],
  requests = [],
  evaluations = [];
const report = { base, fixture, checks: [], screenshots: [], requests };
const profile = (scope, fact) => ({
  id: `ui-fixture-${scope.facade || "W"}`,
  scope,
  title: "Синтетическое положение для UI",
  description:
    "Синтетическое описание модельного положения. Наличие номера неизвестно.",
  conditional: false,
  caveats: ["Синтетическая fixture: количество номеров неизвестно"],
  clauses: [
    {
      text: "Синтетическая фраза для проверки раскрытия основания",
      characteristic_id: fact?.characteristic_id || "sea_view",
      fact_ids: fact ? [fact.id] : [],
      evidence_refs: [],
      status: fact ? "partial" : "unknown",
      synthetic: true,
    },
  ],
});
const fixturePlan = (query, facts = []) =>
  (query.rooms || []).map((room) => ({
    label: room.label,
    scope: { ...room.scope, month: query.month },
    recommendation: "Синтетическое пожелание к расположению",
    status: "unknown",
    verdicts: [],
    caveats: ["Наличие требует ответа отеля"],
    profile: profile({ ...room.scope, month: query.month }, facts[0]),
  }));
let page;
try {
  if (!fixture) {
    const guest = await browser.newContext({
      viewport: { width: 1440, height: 1000 },
      reducedMotion: "reduce",
    });
    const entry = await guest.newPage();
    await blockYandex(entry);
    await entry.goto(base);
    await entry.getByTestId("group-search-example").click();
    assert.equal(await entry.getByTestId("search-input").inputValue(), "");
    await entry.getByTestId("search-input").fill(
      "Едем компанией: Маше — тихий номер; Игорю — вид на море. Для всех — пляж рядом",
    );
    await entry.getByTestId("search-submit").click();
    await entry.getByTestId("room-criteria-1").waitFor({ timeout: 45000 });
    assert.equal(await entry.locator(".room-criteria").count(), 2);
    await entry
      .locator(".result-card .group-plan-row")
      .first()
      .waitFor({ timeout: 45000 });
    await entry.screenshot({
      path: new URL("raw-company-1440.png", artifacts).pathname,
    });
    await entry.setViewportSize({ width: 390, height: 844 });
    assert.equal(
      await entry.evaluate(() => document.documentElement.scrollWidth),
      390,
    );
    await entry.screenshot({
      path: new URL("raw-company-390.png", artifacts).pathname,
    });
    report.checks.push(
      "home help preserves an empty input; an explicitly entered supported group query produces two room editors and real result plans without a saved parsed query",
    );
    await guest.close();
  }
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
    reducedMotion: "reduce",
  });
  await context.addInitScript((query) => {
    if (!sessionStorage.getItem("okno-parsed"))
      sessionStorage.setItem("okno-parsed", JSON.stringify(query));
  }, parsed);
  if (!fixture) {
    const phrases = [
      [
        "нам с мужем тихий номер с видом на море, маме без лестниц до пляжа и не выше 3-го этажа, детям площадка рядом",
        3,
      ],
      ["два номера: один с видом на море, второй тихий во двор", 2],
      [
        "едем вчетвером: родителям тихий номер, нам с другом вид на горы, всем до пляжа не больше 10 минут",
        2,
      ],
      [
        "семья из 5 человек, бабушке низкий этаж, остальным не принципиально, с собакой",
        2,
      ],
    ];
    for (const [text, count] of phrases) {
      const response = await context.request.post(`${base}/api/search`, {
        data: { text, limit: 3 },
      });
      assert(response.ok(), `Free-text search failed: ${response.status()}`);
      const result = await response.json();
      assert.equal(result.parsed.rooms.length, count, text);
      assert(
        result.results.length,
        "Group search should explain available alternatives",
      );
      assert(result.results.every((hit) => hit.room_plan.length === count));
      if (text.includes("во двор")) {
        assert(
          result.results.every((hit) => hit.group !== "all"),
          "Unknown courtyard orientation cannot satisfy a hard wish",
        );
      }
    }
    report.checks.push(
      "four free-text party examples parse and return independent room plans",
    );
  }
  page = await context.newPage();
  await blockYandex(page);
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("request", (request) => {
    if (request.url().includes("/api/"))
      requests.push({ method: request.method(), url: request.url() });
    if (request.url().includes("/evaluate") && request.method() === "POST")
      evaluations.push(request.postDataJSON());
    assert(
      !/\/api\/(passports|questions)/.test(request.url()) ||
        request.method() === "GET",
      "Read-only smoke must not write passports or questions",
    );
  });
  page.on("response", (response) => {
    if (response.url().startsWith(base) && response.status() >= 400)
      failures.push(`${response.status()} ${response.url()}`);
  });
  if (fixture) {
    await page.route("**/api/search", async (route) => {
      const body = route.request().postDataJSON(),
        query = body.parsed || parsed;
      const single = { ...query };
      delete single.rooms;
      const response = await route.fetch({
        postData: JSON.stringify({ ...body, parsed: single }),
      });
      const data = await response.json();
      assert(response.ok(), JSON.stringify(data));
      await route.fulfill({
        response,
        json: {
          ...data,
          parsed: query,
          results: data.results.map((hit) => ({
            ...hit,
            room_plan: fixturePlan(query),
          })),
        },
      });
    });
    await page.route("**/api/hotels/*/evaluate?*", async (route) => {
      const query = route.request().postDataJSON(),
        single = { ...query };
      delete single.rooms;
      const response = await route.fetch({ postData: JSON.stringify(single) }),
        data = await response.json();
      await route.fulfill({
        response,
        json: { ...data, room_plan: fixturePlan(query, data.facts) },
      });
    });
    await page.route("**/api/hotels/*/rooms?*", async (route) => {
      const url = new URL(route.request().url()),
        id = url.pathname.split("/")[3];
      const detail = await (
        await page.request.get(`${base}/api/hotels/${id}?compact=true`)
      ).json();
      const scope = Object.fromEntries(
        [...url.searchParams].map(([key, value]) => [
          key,
          key === "facade" ? value : Number(value),
        ]),
      );
      await route.fulfill({
        json: [
          profile(
            { facade: "W", floor_min: 1, floor_max: 1, month: 9, ...scope },
            detail.facts?.[0],
          ),
        ],
      });
    });
  }
  await page.goto(`${base}/`);
  await page.getByTestId("group-search-example").click();
  assert.equal(await page.getByTestId("search-input").inputValue(), "");
  await page.getByTestId("company-search-help").waitFor();
  assert.equal(await page.locator(".scenario-card").count(), 6);
  report.checks.push("Home example and existing six scenarios");
  const initialResponse = page.waitForResponse(
    (response) =>
      response.url().includes("/api/search") &&
      response.request().method() === "POST",
  );
  await page.goto(`${base}/search?q=${encodeURIComponent(parsed.text)}&edit=1`);
  const initial = (await (await initialResponse).json()).parsed;
  await page.locator(".result-card").first().waitFor({ timeout: 45000 });
  assert.doesNotMatch(
    await page.locator(".search-heading-meta").innerText(),
    /Даты не указаны/,
    "Restored parsed dates must appear in the heading",
  );
  await page
    .getByTestId("room-criteria-0")
    .getByRole("button", {
      name: "Маша — переместить Закат из окна в обязательные",
      exact: true,
    })
    .click();
  await page
    .getByTestId("room-criteria-0")
    .getByRole("button", { name: "Маша — убрать Тишина ночью", exact: true })
    .click();
  const changedResponse = page.waitForResponse(
    (response) =>
      response.url().includes("/api/search") &&
      response.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Применить", exact: true }).click();
  const changed = await (await changedResponse).json();
  assert.deepEqual(
    changed.parsed.rooms[1],
    initial.rooms[1],
    "Editing Masha preserves Igor",
  );
  assert.deepEqual(
    changed.parsed.rooms[0].must.map((c) => c.characteristic_id),
    [
      ...initial.rooms[0].must
        .filter((c) => c.characteristic_id !== "quiet_night")
        .map((c) => c.characteristic_id),
      "sunset_view",
    ],
  );
  assert.equal(changed.parsed.rooms[0].nice.length, 0);
  for (const key of ["no_data", "no_data_must", "unmapped"])
    assert.deepEqual(changed.parsed.rooms[0][key], initial.rooms[0][key]);
  for (const key of ["check_in", "check_out", "must"])
    assert.deepEqual(changed.parsed[key], initial[key]);
  report.checks.push(
    "Room edits preserve other rooms, common criteria, dates and unknowns",
  );
  const card = page.locator(".result-card").first();
  assert.equal(await card.locator(".group-plan-row").count(), 2);
  await card.getByTestId("open-group-room-1").click();
  await page.getByTestId("room-lab-person").waitFor({ timeout: 45000 });
  assert.match(await page.getByTestId("room-lab-person").innerText(), /Игорь/);
  await page.waitForFunction(
    () =>
      document.querySelector('[data-testid="room-lab-verdicts"]') ||
      document.querySelector('[data-testid="room-lab-scope-conflict"]'),
  );
  const own = evaluations
    .filter(
      (query) => query.rooms?.length === 0 && query.text.startsWith("Игорь:"),
    )
    .at(-1);
  assert(own, "RoomLab evaluates selected personal request");
  assert(own.must.some((c) => c.characteristic_id === "sea_view"));
  assert(
    ![...own.must, ...own.nice].some((c) =>
      ["sunset_view", "quiet_night"].includes(c.characteristic_id),
    ),
    "Other room's conditions must not leak",
  );
  assert.equal(own.check_in, parsed.check_in);
  assert.equal(own.check_out, parsed.check_out);
  report.checks.push(
    "RoomLab sends selected room plus shared criteria and dates",
  );
  const goals = page
    .getByTestId("hotel-room-profiles")
    .getByTestId("room-advice-card");
  await revealAncestors(goals.first());
  await goals.first().waitFor({ timeout: 45000 });
  assert(
    (await goals.count()) <= 4,
    "At most four useful goals, not a wall of room profiles",
  );
  await goals
    .first()
    .getByTestId("room-advice-computed")
    .locator(":scope > summary")
    .click();
  const evidence = goals.first().getByTestId("room-advice-evidence").first();
  await evidence.waitFor();
  const factId = await evidence.getAttribute("data-fact-id");
  await evidence.click();
  await page.getByRole("dialog").waitFor();
  assert(
    requests.some(({ url }) =>
      url.includes(`/facts/${encodeURIComponent(factId)}/evidence`),
    ),
  );
  await page.getByRole("button", { name: "Закрыть", exact: true }).click();
  report.checks.push("Useful room advice reveals exact fact ID evidence");
  const hotelUrl = page.url(),
    hotelId = new URL(hotelUrl).pathname.split("/")[2];
  const hotel = await (
    await page.request.get(`${base}/api/hotels/${hotelId}?compact=true`)
  ).json();
  if (hotel.hotel.floors !== null)
    assert.doesNotMatch(
      await page.getByTestId("group-plan").first().innerText(),
      /условный уровень 1/,
      "Conditional model descriptions must not erase known hotel floors",
    );
  await page.goto(`${base}/booking/${hotelId}`);
  await page.getByTestId("group-passport-preview").waitFor({ timeout: 45000 });
  assert.equal(
    await page.locator('[data-testid^="passport-room-request-"]').count(),
    2,
  );
  assert.match(
    await page.locator(".booking-page").innerText(),
    /не резервирует несколько свободных номеров/,
  );
  report.checks.push(
    "Passport retains separate requests without submitting state",
  );
  for (const [width, theme] of [
    [1440, "light"],
    [390, "light"],
    [390, "dark"],
  ]) {
    await page.setViewportSize({ width, height: 1000 });
    await page.evaluate(
      (value) => localStorage.setItem("okno-theme", value),
      theme,
    );
    await page.goto(hotelUrl);
    await page.reload();
    await page.getByTestId("group-plan").first().waitFor({ timeout: 45000 });
    assert.equal(
      await page.locator("[data-theme]").first().getAttribute("data-theme"),
      theme,
    );
    assert(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
      `${width}px ${theme} overflow`,
    );
    await page.getByTestId("group-plan").first().scrollIntoViewIfNeeded();
    const name = `${fixture ? "fixture" : "live"}-hotel-${width}-${theme}.png`;
    await page.screenshot({
      path: new URL(name, artifacts).pathname,
      fullPage: true,
    });
    report.screenshots.push(name);
    const planName = name.replace(".png", "-plan.png");
    await page
      .getByTestId("group-plan")
      .first()
      .screenshot({ path: new URL(planName, artifacts).pathname });
    report.screenshots.push(planName);
    for (const [screen, path, hook] of [
      [
        "search",
        `/search?q=${encodeURIComponent(parsed.text)}&edit=1`,
        "room-criteria-0",
      ],
      ["booking", `/booking/${hotelId}`, "group-passport-preview"],
    ]) {
      await page.goto(`${base}${path}`);
      await page.getByTestId(hook).waitFor({ timeout: 45000 });
      assert(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth,
        ),
        `${screen} ${width}px ${theme} overflow`,
      );
      await page.getByTestId(hook).scrollIntoViewIfNeeded();
      const screenName = `${fixture ? "fixture" : "live"}-${screen}-${width}-${theme}.png`;
      await page.screenshot({ path: new URL(screenName, artifacts).pathname });
      report.screenshots.push(screenName);
    }
  }
  const beforeConflict = evaluations.length;
  await page.evaluate(() => {
    const query = JSON.parse(sessionStorage.getItem("okno-parsed"));
    query.facade = "W";
    query.min_floor = 5;
    sessionStorage.setItem("okno-parsed", JSON.stringify(query));
  });
  await page.goto(hotelUrl);
  await page.getByTestId("room-lab-scope-conflict").waitFor({ timeout: 45000 });
  assert.match(
    await page.getByTestId("room-lab-scope-conflict").innerText(),
    /Сторона исходного запроса/,
  );
  assert.equal(
    evaluations
      .slice(beforeConflict)
      .filter(
        (query) => query.rooms?.length === 0 && query.text.startsWith("Игорь:"),
      ).length,
    0,
    "Conflicting shared restrictions must stop personal evaluation",
  );
  report.checks.push(
    "Shared side/floor constraints remain visible and stop incompatible personal evaluation",
  );
  assert.equal(errors.length, 0, errors.join("\n"));
  assert.equal(failures.length, 0, failures.join("\n"));
  report.checks.push(
    "1440px and 390px light/dark without overflow or browser errors",
  );
  await writeFile(
    new URL(`${fixture ? "fixture" : "live"}-report.json`, artifacts),
    JSON.stringify(report, null, 2),
  );
  console.log(
    JSON.stringify(
      {
        fixture,
        checks: report.checks,
        screenshots: report.screenshots,
        requests: requests.length,
      },
      null,
      2,
    ),
  );
} finally {
  if (page) await page.unrouteAll({ behavior: "ignoreErrors" });
  if (page) await page.request.dispose();
  await closeSmokeBrowser(browser);
}

process.exit(process.exitCode || 0);
