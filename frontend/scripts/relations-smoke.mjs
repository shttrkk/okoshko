import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { mkdir } from "node:fs/promises";
import { chromium } from "playwright";

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
const watch = (page) => {
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
try {
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1000 },
  });
  watch(page);
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
  await page.route("https://yandex.ru/**", (route) =>
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
        body.text === "" &&
        body.limit === 3 &&
        !body.parsed,
    ),
    "Related requests use a separate profile query",
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
    /не подтверждают их совместимость в одном номере/,
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
  watch(fixture);
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
  assert.match(await heatCell.getAttribute("title"), /0,2–0,33 из 1/);
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
    /Проезд на коляске не подтверждён/,
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
        ],
        relatedCount: ids.length,
        requests: requests.length,
        screenshots: artifacts.pathname,
      },
      null,
      2,
    ),
  );
} finally {
  await browser.close();
}
