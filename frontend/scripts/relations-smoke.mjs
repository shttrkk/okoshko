import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { mkdir } from "node:fs/promises";
import { chromium } from "playwright";
import {
  blockYandex,
  openTechnicalFact,
  revealAncestors,
  closeSmokeBrowser,
} from "./panel-guest-smoke-ui.mjs";

const base = process.env.OKNO_BASE_URL || "http://localhost:8080";
const chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const executablePath =
  process.env.OKNO_BROWSER_EXECUTABLE ||
  (existsSync(chrome) ? chrome : undefined);
const browser = await chromium.launch({ headless: true, executablePath });
const artifacts = new URL(
  "../../runtime/night/relations-smoke/",
  import.meta.url,
);
await mkdir(artifacts, { recursive: true });
const normalize = (name) =>
  name
    .toLocaleLowerCase("ru")
    .replaceAll("ё", "е")
    .replace(/[^\p{L}\p{N}]+/gu, "");
const azimuths = {
  N: 0,
  NE: 45,
  E: 90,
  SE: 135,
  S: 180,
  SW: 225,
  W: 270,
  NW: 315,
};
const errors = [],
  failures = [],
  requests = [],
  relatedBodies = [];
const watch = async (page) => {
  await blockYandex(page);
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("response", (response) => {
    if (response.status() >= 400 && response.url().startsWith(base))
      failures.push(`${response.status()} ${response.url()}`);
  });
  page.on("request", (request) => {
    requests.push(request.url());
    if (request.url().endsWith("/api/search") && request.method() === "POST")
      relatedBodies.push(request.postDataJSON());
  });
};
async function checkRoomLab(seed) {
  const hotelId = "osm-way-201474330"; // Brevis has shipped per-floor visibility.
  const sea = {
    characteristic_id: "sea_view",
    label: "Вид на море",
    field: "available",
    op: "eq",
    value: true,
    weight: 1,
    scope: null,
  };
  const walk = {
    characteristic_id: "beach_walk",
    label: "До пляжа до 8 минут без лестниц",
    field: "minutes",
    op: "lte",
    value: 8,
    weight: 1,
    no_stairs: true,
    no_crossings: false,
    scope: null,
  };
  const original = {
    ...structuredClone(seed),
    text: "Море из окна и до пляжа 8 минут без лестниц",
    must: [sea, walk],
    nice: [],
    facade: null,
    min_floor: null,
    month: 9,
    check_in: "2027-09-13",
    check_out: "2027-09-19",
    no_data: [],
    no_data_must: [],
  };
  const evaluationBodies = [];
  const open = async (
    query,
    suffix = "",
    viewport = { width: 1440, height: 1000 },
  ) => {
    const page = await browser.newPage({ viewport });
    await watch(page);
    page.on("request", (request) => {
      if (request.url().includes(`/api/hotels/${hotelId}/evaluate`))
        evaluationBodies.push(request.postDataJSON());
    });
    if (query)
      await page.addInitScript(
        (value) => sessionStorage.setItem("okno-parsed", JSON.stringify(value)),
        query,
      );
    await page.goto(`${base}/hotel/${hotelId}${suffix}`);
    // Hotel-only requests opt into the same preserved window tool explicitly.
    await revealAncestors(page.getByTestId("room-lab"));
    await page.getByTestId("room-lab").waitFor({ timeout: 45000 });
    if (
      (await page.getByTestId("room-lab").getAttribute("data-open")) !== "true"
    )
      await page.getByTestId("room-lab").locator("summary").first().click();
    await page.getByTestId("room-lab-facade").waitFor();
    return page;
  };
  const choose = async (page, facade, floor, month = 9) => {
    const pending = page.waitForResponse((response) => {
      if (!response.url().includes(`/api/hotels/${hotelId}/evaluate`))
        return false;
      const body = response.request().postDataJSON();
      return (
        body.facade === facade &&
        body.month === month &&
        [...body.must, ...body.nice].every(
          (criterion) =>
            criterion.scope?.floor_min === floor &&
            criterion.scope?.floor_max === floor,
        )
      );
    });
    await page.getByTestId("room-lab-facade").selectOption(facade);
    await page.getByTestId("room-lab-floor").selectOption(String(floor));
    await page.getByTestId("room-lab-month").selectOption(String(month));
    const response = await pending;
    assert(
      response.ok(),
      "Room slice evaluation must remain a valid compact read",
    );
    await page.waitForFunction(
      ({ facade, floor, month }) => {
        const result = document.querySelector(
          '[data-testid="room-lab-verdicts"]',
        );
        return (
          result?.dataset.roomFacade === facade &&
          result.dataset.roomFloor === String(floor) &&
          result.dataset.roomMonth === String(month)
        );
      },
      { facade, floor, month },
    );
    return {
      detail: await response.json(),
      body: response.request().postDataJSON(),
    };
  };
  const room = await open(original);
  const low = await choose(room, "W", 1);
  const lowFact = await room
    .getByTestId("room-lab-fact-sea_view")
    .getAttribute("data-fact-id");
  const lowValue = await room
    .getByTestId("room-lab-fact-sea_view")
    .getAttribute("data-model-value");
  const high = await choose(room, "W", 7);
  const highFact = await room
    .getByTestId("room-lab-fact-sea_view")
    .getAttribute("data-fact-id");
  assert.notEqual(
    lowFact,
    highFact,
    "Changing a real floor selects a different scoped source fact",
  );
  assert.notEqual(
    lowValue,
    await room
      .getByTestId("room-lab-fact-sea_view")
      .getAttribute("data-model-value"),
    "Real per-floor model values change",
  );
  const seaVerdict = (detail) =>
    detail.verdicts.find((verdict) => verdict.characteristic_id === "sea_view");
  assert.notEqual(
    seaVerdict(low.detail).status,
    seaVerdict(high.detail).status,
    "The real scoped API verdict changes between W/1 and W/7",
  );
  assert.notDeepEqual(
    seaVerdict(low.detail).fact_ids,
    seaVerdict(high.detail).fact_ids,
  );
  assert(
    high.body.must.every(
      (criterion) =>
        criterion.scope.facade === "W" &&
        criterion.scope.floor_min === 7 &&
        criterion.scope.floor_max === 7 &&
        criterion.scope.month === 9,
    ),
  );
  assert.deepEqual(
    high.body.must.map((criterion) =>
      Object.fromEntries(
        Object.entries(criterion).filter(([key]) => key !== "scope"),
      ),
    ),
    original.must.map((criterion) =>
      Object.fromEntries(
        Object.entries(criterion).filter(([key]) => key !== "scope"),
      ),
    ),
    "No original hard criterion, threshold or route flag is lost",
  );
  await room
    .locator('.hotel-hero-map [data-map-status="ready"]')
    .waitFor({ timeout: 45000 });
  assert.equal(
    await room
      .locator(".hotel-hero-map .travel-map")
      .getAttribute("data-selected-floor"),
    "7",
  );
  await room.getByTestId("map-facade-focus").waitFor();
  assert.match(await room.getByTestId("map-facade-focus").innerText(), /запад/);
  assert.match(
    await room.getByTestId("room-lab-rays-note").innerText(),
    /нет (?:рисунка|лучей) обзора/,
  );
  await room
    .locator(".hotel-hero-map")
    .screenshot({ path: new URL("room-west-floor7.png", artifacts).pathname });
  const westNoise = await room
    .getByTestId("room-lab-fact-quiet_night")
    .getAttribute("data-model-value");
  assert.equal(
    typeof JSON.parse(westNoise).value.night_db_max,
    "number",
    "Outdoor noise selects the actual dB model, not a simulated quiet answer",
  );
  const east = await choose(room, "E", 7);
  assert.notEqual(
    seaVerdict(east.detail).status,
    seaVerdict(high.detail).status,
    "A different real facade changes the sea verdict",
  );
  assert.notEqual(
    westNoise,
    await room
      .getByTestId("room-lab-fact-quiet_night")
      .getAttribute("data-model-value"),
    "Actual outdoor noise values differ by facade",
  );
  assert.equal(
    await room
      .locator(".hotel-hero-map .travel-map")
      .getAttribute("data-selected-facade"),
    "E",
  );
  await room
    .locator(".hotel-hero-map")
    .screenshot({ path: new URL("room-east-floor7.png", artifacts).pathname });
  const june = await choose(room, "E", 7, 6);
  assert.equal(june.body.check_in, original.check_in);
  assert.equal(june.body.check_out, original.check_out);
  assert.match(
    await room.getByTestId("room-lab-season-preview").innerText(),
    /не оценивает условия на исходные даты/,
  );
  assert.deepEqual(
    await room.evaluate(() =>
      JSON.parse(sessionStorage.getItem("okno-parsed")),
    ),
    original,
    "Room experimentation never changes the global parsed query",
  );
  await room.route(`**/api/hotels/${hotelId}/evaluate?*`, async (route) => {
    const response = await route.fetch();
    if (route.request().postDataJSON().must[0]?.scope?.floor_min === 1)
      await new Promise((resolve) => setTimeout(resolve, 500));
    await route.fulfill({ response });
  });
  const oldRequest = room.waitForRequest(
    (request) =>
      request.url().includes(`/api/hotels/${hotelId}/evaluate`) &&
      request.postDataJSON().must[0]?.scope?.floor_min === 1,
  );
  const oldResponse = room.waitForResponse(
    (response) =>
      response.url().includes(`/api/hotels/${hotelId}/evaluate`) &&
      response.request().postDataJSON().must[0]?.scope?.floor_min === 1,
  );
  await room.getByTestId("room-lab-floor").selectOption("1");
  await oldRequest;
  await choose(room, "E", 7, 6);
  await oldResponse;
  await room.evaluate(
    () =>
      new Promise((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(resolve)),
      ),
  );
  assert.equal(
    await room.getByTestId("room-lab-verdicts").getAttribute("data-room-floor"),
    "7",
    "A late response for floor 1 cannot replace the selected floor 7",
  );
  await room.unrouteAll({ behavior: "wait" });
  const shared = await room.getByTestId("room-lab-share").getAttribute("href");
  assert.deepEqual(
    [...new URL(shared, base).searchParams.keys()].sort(),
    ["room_floor", "room_month", "room_side"],
    "Share URL contains only hotel model coordinates",
  );
  const sharedPage = await open(null, shared.slice(shared.indexOf("?")), {
    width: 390,
    height: 844,
  });
  assert.equal(
    await sharedPage.getByTestId("room-lab-facade").inputValue(),
    "E",
  );
  assert.equal(
    await sharedPage.getByTestId("room-lab-floor").inputValue(),
    "7",
  );
  assert.equal(
    await sharedPage.getByTestId("room-lab-month").inputValue(),
    "6",
  );
  assert.equal(
    await sharedPage.getByTestId("room-lab-verdicts").count(),
    0,
    "A shared slice without a query cannot invent a 0/0 match",
  );
  assert.match(
    await sharedPage.getByTestId("room-lab-query").innerText(),
    /Условия поиска не заданы/,
  );
  assert.equal(
    await sharedPage.evaluate(() => document.documentElement.scrollWidth),
    390,
  );
  await sharedPage
    .getByTestId("room-lab")
    .screenshot({ path: new URL("room-lab-mobile.png", artifacts).pathname });
  await sharedPage.close();
  await room.close();

  const constrained = {
    ...original,
    facade: "W",
    min_floor: 7,
    must: [
      {
        ...sea,
        scope: { type: "floor_range", facade: "W", floor_min: 7, floor_max: 9 },
      },
      walk,
    ],
  };
  const blocked = await open(constrained);
  await blocked.getByTestId("room-lab-verdicts").waitFor({ timeout: 30000 });
  const beforeConflict = evaluationBodies.length;
  await blocked.getByTestId("room-lab-facade").selectOption("E");
  await blocked.getByTestId("room-lab-floor").selectOption("3");
  await blocked.getByTestId("room-lab-scope-conflict").waitFor();
  assert.match(
    await blocked.getByTestId("room-lab-scope-conflict").innerText(),
    /несовместим.*обязательными/s,
  );
  assert.equal(await blocked.getByTestId("room-lab-verdicts").count(), 0);
  assert.equal(
    evaluationBodies.length,
    beforeConflict,
    "An empty hard-scope intersection sends no invalid/relaxed evaluation",
  );
  assert.deepEqual(
    await blocked.evaluate(() =>
      JSON.parse(sessionStorage.getItem("okno-parsed")),
    ),
    constrained,
  );
  await blocked.close();

  for (const [name, query] of [
    ["hotel-only-must", { ...original, must: [walk] }],
    ["only-nice", { ...original, must: [], nice: [sea] }],
  ]) {
    const page = await open(query);
    const result = await choose(page, "W", 7);
    assert(
      result.detail.verdicts.some((verdict) => verdict.fact_ids.length),
      `${name} preserves real evidence in the selected context`,
    );
    assert.equal(result.body.must.length, query.must.length);
    assert.equal(result.body.nice.length, query.nice.length);
    assert(
      [...result.body.must, ...result.body.nice].every(
        (criterion) =>
          criterion.scope.floor_min === 7 && criterion.scope.floor_max === 7,
      ),
    );
    await page.close();
  }
  const connection = {
    characteristic_id: "connectivity",
    label: "Интернет от 60 Мбит/с",
    field: "download_mbps",
    op: "gte",
    value: 60,
    weight: 1,
  };
  const unavailable = {
    ...original,
    must: [walk],
    no_data: [connection],
    no_data_must: [connection],
  };
  const missing = await open(unavailable);
  const missingResult = await choose(missing, "W", 7);
  assert.deepEqual(missingResult.body.no_data, unavailable.no_data);
  assert.deepEqual(missingResult.body.no_data_must, unavailable.no_data_must);
  assert.match(
    await missing.getByTestId("room-lab-unavailable").innerText(),
    /обязательное условие не подтверждено/,
  );
  await missing.close();

  const unknown = await browser.newPage({
    viewport: { width: 390, height: 844 },
  });
  await watch(unknown);
  await unknown.route(`**/api/hotels/${hotelId}?*`, async (route) => {
    const response = await route.fetch();
    const detail = await response.json();
    await route.fulfill({
      json: { ...detail, hotel: { ...detail.hotel, floors: null } },
    });
  });
  await unknown.route(`**/api/hotels/${hotelId}/geo`, async (route) => {
    const response = await route.fetch();
    const geo = await response.json();
    await route.fulfill({
      json: { ...geo, floors: { ...geo.floors, count: 16, modeled_count: 16 } },
    });
  });
  await unknown.goto(
    `${base}/hotel/${hotelId}?room_side=W&room_floor=99&room_month=9`,
  );
  await unknown.getByTestId("room-lab-floor").waitFor({ timeout: 45000 });
  assert.equal(
    await unknown.getByTestId("room-lab-floor").locator("option").count(),
    1,
    "Unknown height cannot create upper hotel floors from a modeled count alone",
  );
  assert.equal(await unknown.getByTestId("room-lab-floor").inputValue(), "1");
  assert.match(
    await unknown.getByTestId("room-lab-floor-note").innerText(),
    /Этажность отеля неизвестна/,
  );
  await unknown.close();
  assert(
    evaluationBodies.every((body) =>
      [...body.must, ...body.nice].every(
        (criterion) =>
          !criterion.scope?.floor_min ||
          !criterion.scope?.floor_max ||
          criterion.scope.floor_min <= criterion.scope.floor_max,
      ),
    ),
    "No invalid floor interval is ever sent",
  );
  return {
    realModel: {
      low: {
        facade: "W",
        floor: 1,
        factId: lowFact,
        verdict: seaVerdict(low.detail).status,
      },
      high: {
        facade: "W",
        floor: 7,
        factId: highFact,
        verdict: seaVerdict(high.detail).status,
      },
      east: { facade: "E", floor: 7, verdict: seaVerdict(east.detail).status },
    },
    evaluationRequests: evaluationBodies.length,
  };
}
try {
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1000 },
  });
  await watch(page);
  const searchResponse = await page.request.post(`${base}/api/search`, {
    data: { text: "тихий номер с видом на закат недалеко от моря", limit: 3 },
  });
  assert(searchResponse.ok(), "Live search must be available");
  const search = await searchResponse.json();
  const reference = search.results[0]?.hotel;
  assert(reference, "The seeded search must have a reference hotel");
  const parsed = search.parsed;
  // An edited criterion must survive a related-profile read and navigation.
  const beach = parsed.must.find(
    (criterion) => criterion.characteristic_id === "beach_walk",
  );
  if (beach) beach.value = 8;
  await page.addInitScript(
    (query) => sessionStorage.setItem("okno-parsed", JSON.stringify(query)),
    parsed,
  );
  await page.route(/yandex\.|ya\.ru|mapillary/i, (route) =>
    route.fulfill({
      contentType: "text/html",
      body: "<p>Panorama embed intercepted by the browser test.</p>",
    }),
  );
  await page.goto(`${base}/hotel/${reference.id}`);
  await page
    .locator('.hotel-hero-map [data-map-status="ready"]')
    .waitFor({ timeout: 45000 });
  await page
    .getByTestId("related-hotels")
    .locator('[role="status"]')
    .waitFor({ state: "hidden", timeout: 45000 });
  const related = page.locator(".related-hotel-card");
  const ids = await related.evaluateAll((nodes) =>
    nodes.map((node) => node.dataset.hotelId),
  );
  const names = (await related.locator("h3").allTextContents()).map(normalize);
  assert(
    ids.length > 0 && ids.length <= 3,
    "Show up to three comparable profiles",
  );
  assert(
    !ids.includes(reference.id),
    "Related hotels exclude the reference itself",
  );
  assert.equal(new Set(ids).size, ids.length, "Related hotel IDs are unique");
  assert(
    !names.includes(normalize(reference.name)),
    "Same-name hotel duplicates are excluded",
  );
  assert.equal(
    new Set(names).size,
    names.length,
    "Related hotel names are unique",
  );
  assert(
    relatedBodies.some(
      (body) =>
        body.similar_to === reference.id &&
        body.text === parsed.text &&
        body.limit === 3 &&
        JSON.stringify(body.parsed?.must) === JSON.stringify(parsed.must) &&
        JSON.stringify(body.parsed?.nice) === JSON.stringify(parsed.nice) &&
        body.check_in === parsed.check_in &&
        body.check_out === parsed.check_out,
    ),
    "Related requests isolate the active edited criteria and dates",
  );
  assert.deepEqual(
    await page.evaluate(() =>
      JSON.parse(sessionStorage.getItem("okno-parsed")),
    ),
    parsed,
    "Related browsing preserves the exact edited query",
  );
  assert.match(
    await page.getByTestId("related-hotels").innerText(),
    /относятся к отелю в целом, а не к одному номеру/,
  );
  assert(
    !requests.some(
      (url) =>
        /\/api\/hotels\//.test(url) &&
        !url.includes(`/api/hotels/${reference.id}`),
    ),
    "Related profiles do not fetch each candidate's detail or evidence",
  );

  const focus = page.locator('[data-testid^="verdict-fact-"]').first();
  await focus.waitFor({ timeout: 10000 });
  const anchor = await focus.getAttribute("aria-controls");
  await focus.click();
  assert(
    await page
      .locator(`#${anchor}`)
      .evaluate((node) => document.activeElement === node),
    "Verdict action focuses its stable fact anchor",
  );
  const mapAction = page
    .locator('[data-testid^="fact-map-sunset_view-"]')
    .first();
  await revealAncestors(mapAction);
  const side = (await mapAction.getAttribute("data-testid")).split("-").at(-1);
  await mapAction.click();
  await page.getByTestId("hotel-map-context").waitFor();
  assert.equal(
    await page
      .locator(".hotel-hero-map .travel-map")
      .getAttribute("data-selected-facade"),
    side,
  );
  assert.equal(
    await page
      .locator(".hotel-hero-map .travel-map")
      .getAttribute("data-selected-trait"),
    "sunset_view",
  );
  assert(
    await page
      .getByTestId("hotel-map-context")
      .getByRole("button", { name: /Спросить/ })
      .count(),
    "The selected map context leads to a question without sending it",
  );
  const evidenceResponse = page.waitForResponse(
    (response) =>
      response.url().includes(`/api/hotels/${reference.id}/facts/`) &&
      response.url().endsWith("/evidence"),
  );
  await page
    .getByTestId("hotel-map-context")
    .getByRole("button", { name: "Основание", exact: true })
    .click();
  const fullEvidence = await (await evidenceResponse).json();
  assert(
    fullEvidence.fact.supporting_claim_ids.length > 0,
    "The selected fact retains its evidence references",
  );
  assert.equal(
    fullEvidence.fact.id,
    await page.getByTestId("hotel-map-context").getAttribute("data-fact-id"),
  );
  await page
    .getByRole("dialog")
    .locator(".evidence-card summary")
    .first()
    .click();
  await page
    .getByRole("dialog")
    .getByText("Данные получены", { exact: true })
    .waitFor();
  await page.keyboard.press("Escape");
  await page.getByRole("dialog").waitFor({ state: "hidden" });
  const externalBeforeClick = requests.filter(
    (url) => /^https?:/.test(url) && !url.startsWith(base),
  );
  assert.deepEqual(
    externalBeforeClick,
    [],
    "No external requests before an explicit panorama click",
  );
  await page.getByTestId("selected-panorama").click();
  const frame = page.getByRole("dialog").locator("iframe");
  await frame.waitFor();
  const panoramaUrl = new URL(await frame.getAttribute("src"));
  assert.equal(
    panoramaUrl.searchParams.get("panorama[direction]"),
    `${azimuths[side]},0`,
  );
  assert.match(await page.getByRole("dialog").innerText(), /не из номера/);
  assert.equal(
    await page
      .getByRole("dialog")
      .getByTestId("panorama-question")
      .getByRole("button", { name: /Спросить/ })
      .count(),
    1,
    "The panorama leads directly to the selected characteristic's question",
  );
  await page.keyboard.press("Escape");
  await page.getByRole("dialog").waitFor({ state: "hidden" });
  await page
    .locator('[data-testid="map-poi-beach"]')
    .first()
    .click({ force: true });
  await page.getByTestId("map-poi-popup").waitFor();
  await page.screenshot({
    path: new URL("desktop.png", artifacts).pathname,
    fullPage: true,
  });
  await related.first().getByRole("link", { name: "Посмотреть факты" }).click();
  await page.locator(".hotel-title-row h1").waitFor();
  assert.deepEqual(
    await page.evaluate(() =>
      JSON.parse(sessionStorage.getItem("okno-parsed")),
    ),
    parsed,
    "Hotel-to-hotel navigation preserves the edited query",
  );

  // Browser-only contract fixtures check branches unavailable in every hotel.
  const detailResponse = await page.request.get(
    `${base}/api/hotels/${reference.id}?compact=true`,
  );
  const detail = await detailResponse.json();
  const geoResponse = await page.request.get(
    `${base}/api/hotels/${reference.id}/geo`,
  );
  const geo = await geoResponse.json();
  const template = detail.facts.find((fact) => fact.value !== null);
  assert(template, "A real fact provides the fixture's provenance envelope");
  const make = (id, value, scope = { type: "hotel" }, status = "computed") => ({
    ...template,
    id: `browser-fixture-${id}`,
    characteristic_id: id,
    scope,
    value,
    status,
    synthetic: true,
    confidence: 0.54,
    valid_until: "2100-01-01T00:00:00Z",
  });
  const fixtures = [
    make(
      "morning_sun",
      {
        available: true,
        visible_minutes: 20,
        visible_minutes_min: 20,
        visible_minutes_max: 41,
        exposure_index: 0.2,
      },
      {
        type: "floor_range",
        facade: "E",
        floor_min: 2,
        floor_max: 5,
        month: 9,
      },
    ),
    make(
      "evening_heat",
      {
        available: true,
        visible_minutes_min: 35,
        visible_minutes_max: 66,
        exposure_index: 0.33333,
        exposure_index_min: 0.2,
        exposure_index_max: 0.33333,
      },
      {
        type: "floor_range",
        facade: "W",
        floor_min: 1,
        floor_max: 4,
        month: 9,
      },
    ),
    make(
      "railway_noise",
      { night_db_min: 40, night_db_max: 55 },
      { type: "facade", facade: "N" },
      "disputed",
    ),
    make("beach_surface", {
      available: null,
      surface: null,
      distance_m: 400,
      selection_kind: "beach_walk_destination",
    }),
    make("accessible_route", {
      available: null,
      wheelchair_suitable: null,
      has_steps: false,
      wheelchair_tag_coverage: 0.2,
    }),
    make("green_nearby", {
      available: null,
      distance_m: 1200,
      leisure: "park",
    }),
  ];
  const route = geo.route?.features.find(
    (feature) => feature.geometry.type === "LineString",
  );
  const endpoint = route?.geometry.coordinates.at(-1);
  assert(
    endpoint,
    "The reference has an actual route endpoint for the map fixture",
  );
  const unsafeName = '<img src=x onerror="window.__poiExecuted=true">';
  const fixtureGeo = {
    ...geo,
    nearby: {
      type: "FeatureCollection",
      features: [
        {
          type: "Feature",
          geometry: { type: "Point", coordinates: endpoint },
          properties: {
            kind: "beach",
            name: unsafeName,
            osm_id: "fixture-existing-coordinate",
          },
        },
        {
          type: "Feature",
          geometry: null,
          properties: {
            kind: "pharmacy",
            osm_id: "no-coordinates",
            distance_m: 100,
          },
        },
      ],
    },
  };
  const fixture = await browser.newPage({
    viewport: { width: 390, height: 844 },
    isMobile: true,
    hasTouch: true,
  });
  await watch(fixture);
  await fixture.route(`**/api/hotels/${reference.id}?*`, (route) =>
    route.fulfill({
      json: {
        ...detail,
        facts: [
          ...detail.facts.filter(
            (fact) =>
              !fixtures.some(
                (item) => item.characteristic_id === fact.characteristic_id,
              ),
          ),
          ...fixtures,
        ],
      },
    }),
  );
  await fixture.route(`**/api/hotels/${reference.id}/geo`, (route) =>
    route.fulfill({ json: fixtureGeo }),
  );
  await fixture.goto(`${base}/hotel/${reference.id}`);
  await fixture
    .locator('.hotel-hero-map [data-map-status="ready"]')
    .waitFor({ timeout: 45000 });
  for (const fact of fixtures)
    await fixture.getByTestId(`fact-card-${fact.characteristic_id}`).waitFor();
  for (const fact of fixtures)
    await openTechnicalFact(
      fixture.getByTestId(`fact-card-${fact.characteristic_id}`),
    );
  assert.equal(await fixture.getByTestId("solar-proxy-matrix").count(), 2);
  assert(
    (
      await fixture
        .getByTestId("solar-proxy-matrix")
        .first()
        .locator("tbody tr")
        .boundingBox()
    ).height < 50,
    "Missing-month cells keep a compact table row and align with month headers",
  );
  assert.doesNotMatch(
    (await fixture.getByTestId("solar-proxy-matrix").allTextContents()).join(
      " ",
    ),
    /вероятность|Monte Carlo/,
  );
  const heatCell = fixture
    .getByTestId("fact-card-evening_heat")
    .getByRole("button", { name: /вечернее прямое солнце 35–66 мин/ });
  assert.match(await heatCell.getAttribute("title"), /вечернее прямое солнце 35–66 мин/);
  assert.doesNotMatch(await heatCell.getAttribute("title"), /из 1|индекс/);
  assert.match(
    await fixture
      .getByTestId("fact-card-railway_noise")
      .locator(".fact-headline")
      .innerText(),
    /противоречат/,
  );
  assert.match(
    await fixture.getByTestId("fact-card-beach_surface").innerText(),
    /Покрытие пляжа не указано/,
  );
  assert.match(
    await fixture.getByTestId("fact-card-accessible_route").innerText(),
    /Данных о проезде с коляской мало/,
  );
  assert.match(
    await fixture.getByTestId("fact-card-accessible_route").innerText(),
    /20% рёбер/,
  );
  assert.match(
    await fixture.getByTestId("fact-card-green_nearby").innerText(),
    /1,2 км по прямой/,
  );
  assert.equal(
    await fixture.getByTestId("map-poi-pharmacy").count(),
    0,
    "An ID and distance without coordinates must not create a point",
  );
  await fixture.getByTestId("map-poi-beach").click({ force: true });
  await fixture.getByTestId("map-poi-popup").waitFor();
  assert.match(
    await fixture.getByTestId("map-poi-popup").innerText(),
    /<img src=x/,
  );
  assert.equal(
    await fixture.getByTestId("map-poi-popup").locator("img").count(),
    0,
    "POI names stay literal DOM text",
  );
  assert.equal(await fixture.evaluate(() => window.__poiExecuted), undefined);
  assert.equal(
    await fixture.evaluate(() => document.documentElement.scrollWidth),
    390,
    "Mobile detail layout must not overflow",
  );
  await fixture.screenshot({
    path: new URL("mobile-traits.png", artifacts).pathname,
    fullPage: true,
  });
  await fixture
    .getByTestId("fact-card-morning_sun")
    .screenshot({ path: new URL("morning-card.png", artifacts).pathname });
  await fixture
    .getByTestId("fact-card-evening_heat")
    .screenshot({ path: new URL("heat-card.png", artifacts).pathname });
  const roomLab = await checkRoomLab(parsed);
  assert(
    !requests.some((url) => /\/seed\/(pois|beaches|green)\.geojson/.test(url)),
    "A hotel map must not fetch regional POI layers",
  );
  assert.deepEqual(errors, [], "No uncaught browser errors");
  assert.deepEqual(
    failures,
    [],
    "No broken same-origin assets or API responses",
  );
  console.log(
    JSON.stringify(
      {
        status: "passed",
        base,
        checks: [
          "profile-related-with-query-preservation",
          "unique-related-hotels",
          "verdict-fact-focus",
          "fact-facade-map",
          "lazy-evidence-references",
          "directed-lazy-panorama",
          "map-question-action",
          "actual-coordinate-pois",
          "six-trait-semantics",
          "safe-poi-dom",
          "mobile-no-overflow",
          "room-lab-real-floor-facade-verdicts",
          "room-lab-hard-scope-date-preservation",
          "room-lab-stale-response-guard",
          "room-lab-share-unknown-height",
          "room-lab-hotel-only-nice-unavailable",
        ],
        relatedCount: ids.length,
        roomLab,
        requests: requests.length,
        screenshots: artifacts.pathname,
      },
      null,
      2,
    ),
  );
} finally {
  await closeSmokeBrowser(browser);
}

process.exit(process.exitCode || 0);
