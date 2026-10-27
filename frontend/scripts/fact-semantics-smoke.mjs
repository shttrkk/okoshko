import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { mkdir, readFile } from "node:fs/promises";
import { build } from "esbuild";
import { chromium } from "playwright";
import {
  blockYandex,
  openTechnicalFact,
  closeSmokeBrowser,
} from "./panel-guest-smoke-ui.mjs";

// All API, evidence and hotel data are local browser fixtures, never 8080.
const base = process.env.OKNO_BASE_URL || "http://localhost:5178";
const chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const executablePath =
  process.env.OKNO_BROWSER_EXECUTABLE ||
  (existsSync(chrome) ? chrome : undefined);
const artifacts = new URL(
  "../../runtime/night/fact-semantics-smoke/",
  import.meta.url,
);
await mkdir(artifacts, { recursive: true });
const collection = { type: "FeatureCollection", features: [] };
const hotel = {
  id: "source-semantics-fixture",
  name: "Проверка источников, browser fixture",
  lat: 43.58,
  lon: 39.72,
  address: "Синтетическая browser fixture",
  stars: null,
  price_rub: null,
  basic_fields_source: "fixture",
  source_ids: [],
  floors: 12,
  footprint: null,
  image: null,
  image_attribution: null,
  website: null,
  room_types: [],
  tags: {},
};
const scope = {
  type: "floor_range",
  facade: "W",
  floor_min: 7,
  floor_max: 7,
  month: 6,
};
const makeFact = (id, characteristic_id, value, ownScope = scope) => ({
  id,
  hotel_id: hotel.id,
  characteristic_id,
  scope: ownScope,
  value,
  confidence: 0.85,
  status: "computed",
  resolution: "Browser fixture",
  supporting_claim_ids: [`claim-${id}`],
  conflicting_claim_ids: [],
  evidence: [],
  updated_at: "2026-10-27T00:00:00Z",
  valid_until: "2028-01-01T00:00:00Z",
  synthetic: true,
});
const geometric = makeFact(
  "geometry-yes",
  "sea_view",
  { available: true, probability: 0.9, min_floor: 5 },
  { type: "floor_range", facade: "W", floor_min: 5, floor_max: 12 },
);
const geometricNo = makeFact(
  "geometry-no",
  "sea_view",
  { available: false, probability: 0, min_floor: 5 },
  { type: "floor_range", facade: "W", floor_min: 1, floor_max: 4 },
);
const seaFalse = makeFact("report-sea-false", "sea_view", {
  available: false,
  facade: "W",
});
const seaTrue = {
  ...makeFact("report-sea-true", "sea_view", {
    available: true,
    facade: "W",
  }),
  synthetic: false,
};
const quietModel = makeFact(
  "noise-model",
  "quiet_night",
  { available: false, index: "moderate", night_db_min: 40, night_db_max: 56 },
  { type: "facade", facade: "W" },
);
const quietTrue = makeFact("quiet-true", "quiet_night", {
  available: true,
  facade: "W",
});
const quietFalse = makeFact(
  "quiet-false",
  "quiet_night",
  { available: false, facade: "W" },
  { ...scope, month: 7 },
);
const sunsetSeptember = makeFact(
  "sunset-september",
  "sunset_view",
  {
    available: true,
    probability: 1,
    azimuth_deg: 274.678,
    min_floor: 1,
    visible_minutes: 26.5,
  },
  { type: "floor_range", facade: "N", floor_min: 1, floor_max: 4, month: 9 },
);
const sunsetMay = makeFact(
  "sunset-may",
  "sunset_view",
  {
    available: true,
    probability: 1,
    azimuth_deg: 297.458,
    min_floor: 1,
    visible_minutes: 30.5,
  },
  { ...sunsetSeptember.scope, month: 5 },
);
const specs = [
  {
    id: "sea_view",
    name_ru: "Вид на море",
    category: "Вид",
    scope: "floor_range",
  },
  {
    id: "sunset_view",
    name_ru: "Закат из окна",
    category: "Вид",
    scope: "floor_range",
  },
  {
    id: "quiet_night",
    name_ru: "Тишина ночью",
    category: "Сон",
    scope: "facade",
  },
];
const geo = {
  floors: { count: 16, modeled_count: 8 },
  view_cones: collection,
  route: collection,
  nearby: collection,
  monte_carlo: { iterations: 64, probability: 0.5 },
  facades: ["N", "W"].map((code) => ({
    code,
    azimuth_deg: code === "N" ? 340.9590477273491 : 270,
    geometry: {
      type: "LineString",
      coordinates: [
        [39.72, 43.58],
        [39.7201, 43.5801],
      ],
    },
    sea_probability_by_floor: Array.from({ length: 16 }, (_, index) =>
      index < 4 ? 0 : 0.921875,
    ),
    min_floor_sea_p80: 7,
    sunset_by_month: [
      { month: 9, date: "2026-09-15", visible_minutes: Array(16).fill(26) },
    ],
  })),
};
const emptyParsed = {
  text: "",
  must: [],
  nice: [],
  no_data: [],
  no_data_must: [],
  month: 9,
  check_in: null,
  check_out: null,
  unmapped: [],
  clarifications: [],
  mode: "offline",
};
const errors = [],
  failures = [],
  requests = [],
  checks = [];
const browser = await chromium.launch({ executablePath, headless: true });
let mode = "false",
  revision = 0,
  unknownFloors = false;
const facts = () => {
  const view =
    mode === "model"
      ? [geometricNo, geometric]
      : mode === "true"
        ? [geometricNo, geometric, seaTrue]
        : mode === "disputed"
          ? [{ ...geometric, status: "disputed" }]
          : mode === "stale"
            ? [{ ...geometric, status: "stale" }]
            : [geometricNo, geometric, seaFalse];
  return [
    ...view,
    quietModel,
    quietTrue,
    quietFalse,
    sunsetSeptember,
    sunsetMay,
  ];
};
const profile = () => ({
  hotel: { ...hotel, floors: unknownFloors ? null : hotel.floors },
  facts: facts(),
  claims: [],
  conflicts: [],
  questions: [],
  verdicts: [],
  geo: {},
  room_recommendation: "Расположение окна и наличие номера нужно уточнить.",
  disappointments: [],
  evidence_deferred: true,
  geometry_deferred: true,
});
const fullEvidence = (fact) => {
  const model = fact.id.startsWith("geometry") || fact.id === quietModel.id;
  const item = {
    id: `evidence-${fact.id}`,
    kind: model ? "calculation" : "simulation",
    label: "Синтетическая browser fixture",
    source_ids: [
      model ? "geo_engine" : fact.synthetic ? "hotelier_simulator" : "hotelier",
    ],
    quote: model
      ? null
      : `Источник fixture: available=${fact.value.available}; числовой уровень шума не задан.`,
    inputs: { fixture_only: true },
    output: fact.value,
    limitations: ["Только проверка интерфейса"],
  };
  return {
    fact: { ...fact, evidence: [item] },
    claims: [
      {
        id: `claim-${fact.id}`,
        hotel_id: hotel.id,
        characteristic_id: fact.characteristic_id,
        scope: fact.scope,
        value: fact.value,
        source: item.source_ids[0],
        method: model ? "geo_fixture" : "hotelier_answer@1",
        evidence: [item],
        confidence: fact.confidence,
        observed_at: fact.updated_at,
        valid_until: fact.valid_until,
        synthetic: true,
      },
    ],
  };
};
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
    const req = route.request(),
      path = new URL(req.url()).pathname;
    requests.push({ method: req.method(), path });
    if (path === "/api/revision")
      return route.fulfill({
        json: { instance: "semantics-fixture", revision },
      });
    if (path === "/api/status")
      return route.fulfill({
        json: {
          name: "Browser fixture",
          mode: "offline",
          version: "fixture",
          hotel_count: 1,
          characteristic_count: specs.length,
          seed_version: "fixture",
        },
      });
    if (path === "/api/hotels")
      return route.fulfill({
        json: [{ ...hotel, floors: unknownFloors ? null : hotel.floors }],
      });
    if (path.endsWith("/room-advice")) return route.fulfill({ json: [] });
    if (path.endsWith("/rooms")) return route.fulfill({ json: [] });
    if (path === "/api/characteristics") return route.fulfill({ json: specs });
    if (path === "/api/search")
      return route.fulfill({
        json: {
          parsed: emptyParsed,
          results: [],
          total: 0,
          latency_ms: 1,
          run_id: null,
          mode: "offline",
        },
      });
    if (path.endsWith("/geo"))
      return route.fulfill({
        json: unknownFloors
          ? { ...geo, floors: { count: 16, modeled_count: 16 } }
          : geo,
      });
    if (path.endsWith("/evidence")) {
      const factId = path.split("/").at(-2),
        fact = facts().find((f) => f.id === factId);
      assert.ok(fact, `Known fixture evidence ${factId}`);
      return route.fulfill({ json: fullEvidence(fact) });
    }
    if (path === `/api/hotels/${hotel.id}` || path.endsWith("/evaluate"))
      return route.fulfill({ json: profile() });
    throw new Error(`Unexpected fixture API: ${req.method()} ${path}`);
  });
  const open = async () => {
    const page = await context.newPage();
    await blockYandex(page);
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("response", (res) => {
      if (res.url().startsWith(base) && res.status() >= 400)
        failures.push(`${res.status()} ${res.url()}`);
    });
    await page.goto(
      `${base}/hotel/${hotel.id}?room_side=W&room_floor=7&room_month=6`,
    );
    await page.getByTestId("room-lab-fact-sea_view").waitFor();
    return page;
  };
  const page = await open();
  const seaCard = page.getByTestId("room-lab-fact-sea_view");
  assert.equal(await seaCard.getAttribute("data-fact-id"), seaFalse.id);
  assert.match(await seaCard.innerText(), /Не виден/);
  assert.doesNotMatch(await seaCard.innerText(), /92%|вероятность|симуляций/);
  assert.equal(
    JSON.parse(await seaCard.getAttribute("data-model-value")).seaProbability,
    null,
  );
  await page.getByTestId("room-lab-evidence-sea_view").click();
  await page.getByRole("dialog").locator(".evidence-card").waitFor();
  assert.match(await page.getByRole("dialog").innerText(), /Не виден/);
  assert.match(await page.getByRole("dialog").innerText(), /available=false/);
  await page.screenshot({
    path: new URL("boolean-sea-evidence.png", artifacts).pathname,
  });
  await page.keyboard.press("Escape");
  checks.push(
    "scoped hotelier false stays false and shares evidence with the displayed value; unrelated Geo 92% is not substituted",
  );
  const quietCard = page.getByTestId("fact-card-quiet_night");
  assert.doesNotMatch(await quietCard.innerText(), /40[–-]56.*дБ/);
  await openTechnicalFact(quietCard);
  const positiveReport = page.getByTestId(`quiet-report-${quietTrue.id}`);
  const negativeReport = page.getByTestId(`quiet-report-${quietFalse.id}`);
  assert.match(
    await positiveReport.innerText(),
    /По сообщению источника: тихо/,
  );
  assert.match(
    await negativeReport.innerText(),
    /По сообщению источника: не тихо/,
  );
  assert.match(await positiveReport.innerText(), /7-й этаж.*июнь/s);
  assert.match(await negativeReport.innerText(), /7-й этаж.*июль/s);
  assert.doesNotMatch(await positiveReport.innerText(), /нет данных|\d+\s*дБ/);
  assert.doesNotMatch(await negativeReport.innerText(), /нет данных|\d+\s*дБ/);
  await negativeReport.click();
  await page.getByRole("dialog").locator(".evidence-card").waitFor();
  assert.match(
    await page.getByRole("dialog").innerText(),
    /По сообщению источника: не тихо/,
  );
  assert.match(await page.getByRole("dialog").innerText(), /available=false/);
  await page.keyboard.press("Escape");
  await page
    .getByTestId("fact-card-quiet_night")
    .screenshot({ path: new URL("quiet-reports.png", artifacts).pathname });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(
    await page.evaluate(() => document.documentElement.scrollWidth),
    390,
  );
  await page
    .getByTestId("fact-card-quiet_night")
    .screenshot({ path: new URL("quiet-mobile-390.png", artifacts).pathname });
  checks.push(
    "quiet true/false reports are separate scoped source messages with synthetic flags, alongside the outdoor model, without invented dB",
  );
  await page.close();
  mode = "true";
  const truePage = await open();
  assert.equal(
    await truePage
      .getByTestId("room-lab-fact-sea_view")
      .getAttribute("data-fact-id"),
    seaTrue.id,
  );
  assert.match(
    await truePage.getByTestId("room-lab-fact-sea_view").innerText(),
    /Виден/,
  );
  assert.doesNotMatch(
    await truePage.getByTestId("room-lab-fact-sea_view").innerText(),
    /92%|вероятность|симуляций|Демонстрация/,
  );
  await truePage.getByTestId("room-lab-evidence-sea_view").click();
  await truePage.getByRole("dialog").locator(".evidence-card").waitFor();
  assert.match(
    await truePage.getByRole("dialog").innerText(),
    /available=true/,
  );
  assert.doesNotMatch(
    await truePage.getByRole("dialog").innerText(),
    /Демонстрация/,
  );
  await truePage.close();
  mode = "model";
  const modelPage = await open();
  const modelSea = modelPage.getByTestId("room-lab-fact-sea_view");
  assert.doesNotMatch(await modelSea.innerText(), /\d+%/);
  assert.equal(
    JSON.parse(await modelSea.getAttribute("data-model-value")).seaProbability,
    0.921875,
  );
  await modelSea.locator("summary").click();
  assert.match(
    await modelSea.innerText(),
    /Вероятность вида на море по расчёту: 92%/,
  );
  await modelPage.getByTestId("room-lab-floor").selectOption("1");
  const lowProbability = modelPage
    .getByTestId("room-lab-fact-sea_view")
    .locator("details")
    .first();
  if ((await lowProbability.getAttribute("open")) === null)
    await lowProbability.locator(":scope > summary").click();
  assert.match(
    await modelPage.getByTestId("room-lab-fact-sea_view").innerText(),
    /Вероятность вида на море по расчёту: 0%/,
  );
  await modelPage.close();
  for (const state of ["stale", "disputed"]) {
    mode = state;
    const guard = await open();
    assert.doesNotMatch(
      await guard.getByTestId("room-lab-fact-sea_view").innerText(),
      /92%|Виден/,
    );
    assert.match(
      await guard.getByTestId("room-lab-fact-sea_view").innerText(),
      state === "stale" ? /устарели/ : /Источники расходятся/,
    );
    await guard.close();
  }
  checks.push(
    "boolean true remains a report, valid geometry retains per-floor 0/92%, stale and disputed facts do not gain positive model text",
  );
  mode = "model";
  const floorPage = await open();
  await floorPage.getByTestId("show-calculation").click();
  const floor = floorPage.getByTestId("geo-floor");
  await floorPage.waitForFunction(
    () => document.querySelector('[data-testid="geo-floor"]')?.max === "8",
  );
  assert.equal(await floor.inputValue(), "8");
  await floor.fill("99");
  assert.equal(await floor.inputValue(), "8");
  await floor.fill("0");
  assert.equal(await floor.inputValue(), "1");
  await floor.fill("6");
  unknownFloors = true;
  revision++;
  await floorPage.waitForFunction(
    () => document.querySelector('[data-testid="geo-floor"]')?.disabled,
    null,
    { timeout: 10000 },
  );
  assert.equal(await floor.inputValue(), "1");
  assert.equal(await floor.getAttribute("max"), "1");
  assert.match(
    await floorPage.getByTestId("geo-floor-note").innerText(),
    /Этажность отеля неизвестна/,
  );
  assert.match(
    await floorPage.getByRole("dialog").innerText(),
    /условный уровень 1/,
  );
  assert.doesNotMatch(
    await floorPage.getByRole("dialog").innerText(),
    /1-й этаж/,
  );
  await floorPage.screenshot({
    path: new URL("conditional-geo-level.png", artifacts).pathname,
  });
  await floorPage.close();
  checks.push(
    "GeoModal respects known model bounds in handler/state and clamps an open selection to conditional level 1 when hotel height becomes unknown despite Geo count16",
  );

  // Use the stylesheet of the served build, which can differ from local dist.
  const indexResponse = await context.request.get(base);
  assert(indexResponse.ok(), "The served application index must load");
  const html = await indexResponse.text();
  const stylesheet = html.match(/href="([^"]+\.css)"/);
  assert(stylesheet, "The served index must reference a stylesheet");
  const css = new URL(stylesheet[1], base).href;
  const cssResponse = await context.request.get(css);
  assert(cssResponse.ok(), "The served stylesheet must load");
  const seed = {
    facts: [sunsetSeptember, sunsetMay],
    specs,
    hotelId: hotel.id,
    month: 9,
  };
  const bundled = await build({
    stdin: {
      resolveDir: new URL("..", import.meta.url).pathname,
      contents: `import React,{useState} from 'react';import {createRoot} from 'react-dom/client';
      import {FactsPanel,headline} from './src/components/FactsPanel.tsx';import {AppProvider} from './src/state.tsx';
      const seed=${JSON.stringify(seed)};
      function App(){const [normals,setNormals]=useState({N:340.9590477273491});const [facts,setFacts]=useState(seed.facts);
        window.normalFixture={setNormals,evidence:(values)=>setFacts(seed.facts.map(f=>({...f,evidence:values.map((n,i)=>({id:'e'+i,kind:'calculation',label:'Fixture',source_ids:['geo_engine'],inputs:{facade_azimuth_deg:n},output:{},limitations:[]}))}))),summary:()=>headline('sunset_view',facts,9,normals)};
        return React.createElement('div',{className:'page-container'},React.createElement(FactsPanel,{...seed,facts,claims:[],onEvidence:()=>{},facadeAzimuths:normals}));}
      createRoot(document.getElementById('root')).render(React.createElement(AppProvider,null,React.createElement(App)));`,
      loader: "jsx",
    },
    bundle: true,
    // The harness already loads the exact CSS served by the application.
    loader: { ".css": "empty" },
    write: false,
    format: "esm",
    define: { "process.env.NODE_ENV": '"production"' },
  });
  const normalPage = await context.newPage();
  await blockYandex(normalPage);
  normalPage.on("pageerror", (error) => errors.push(error.message));
  await normalPage.route("**/__normal_fixture", (route) =>
    route.fulfill({
      contentType: "text/html",
      body: `<link rel="stylesheet" href="${css}"><div id="root"></div>`,
    }),
  );
  await normalPage.goto(`${base}/__normal_fixture`);
  await normalPage.addScriptTag({
    type: "module",
    content: bundled.outputFiles[0].text,
  });
  const panel = normalPage.getByTestId("fact-card-sunset_view");
  await panel.waitFor();
  await openTechnicalFact(panel);
  const september = panel.getByRole("button", { name: /^север,.*сентябре:/ });
  const may = panel.getByRole("button", { name: /^север,.*мае:/ });
  assert.match(await september.getAttribute("title"), /Закат виден сбоку/);
  assert.equal(
    Math.round(Number(await september.getAttribute("data-sunset-angle"))),
    66,
  );
  const computed = panel
    .getByTestId("sunset-angle-details")
    .locator("xpath=..");
  await computed.locator(":scope > summary").click();
  assert.match(await computed.innerText(), /сентябре[^\n]*66°/);
  assert.match(await computed.innerText(), /мае[^\n]*44°/);
  assert.equal(await september.getAttribute("data-sunset-oblique"), "true");
  assert.equal(await may.getAttribute("data-sunset-oblique"), null);
  assert.equal(
    Math.round(Number(await may.getAttribute("data-sunset-angle"))),
    44,
  );
  assert.match(
    await panel.locator(".fact-headline").innerText(),
    /Закат сбоку: север/,
  );
  await panel.screenshot({
    path: new URL("exact-facade-sunset.png", artifacts).pathname,
  });
  await normalPage.evaluate(() => window.normalFixture.setNormals(undefined));
  await normalPage.waitForFunction(
    () => !document.querySelector("[data-sunset-angle]"),
  );
  assert.match(await computed.innerText(), /точный угол[\s\S]*неизвестен/);
  assert.doesNotMatch(await september.getAttribute("title"), /угол ≈/);
  await normalPage.evaluate(() =>
    window.normalFixture.evidence([340.9590477273491]),
  );
  await normalPage.waitForFunction(() =>
    document.querySelector('[data-sunset-oblique="true"]'),
  );
  assert.equal(
    Math.round(Number(await september.getAttribute("data-sunset-angle"))),
    66,
  );
  await normalPage.evaluate(() => {
    window.normalFixture.setNormals({ N: 340.9590477273491 });
    window.normalFixture.evidence([340.9590477273491, 90]);
  });
  await normalPage.waitForFunction(
    () => !document.querySelector("[data-sunset-angle]"),
  );
  assert.doesNotMatch(await september.getAttribute("title"), /угол ≈/);
  await normalPage.evaluate(() => {
    window.normalFixture.evidence([]);
    window.normalFixture.setNormals({ N: Number.NaN });
  });
  assert.doesNotMatch(await panel.innerText(), /NaN/);
  checks.push(
    "actual facade normal yields September 66° oblique and May44° without oblique; absent/conflicting/nonfinite normals never turn compass N into an exact normal",
  );
  await normalPage.evaluate(() => {
    window.normalFixture.evidence([]);
    window.normalFixture.setNormals({ N: 340.9590477273491 });
  });
  await normalPage.setViewportSize({ width: 390, height: 844 });
  assert.equal(
    await normalPage.evaluate(() => document.documentElement.scrollWidth),
    390,
  );
  await normalPage.screenshot({
    path: new URL("sunset-mobile-390.png", artifacts).pathname,
  });
  checks.push(
    "new source messages and sunset annotations retain the existing layout; mobile390 has no overflow",
  );
  assert.deepEqual(errors, []);
  assert.deepEqual(failures, []);
  assert.ok(
    requests.every((req) => req.method === "GET" || req.path === "/api/search"),
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
        artifacts: artifacts.pathname,
      },
      null,
      2,
    ),
  );
  await context.close();
} finally {
  await closeSmokeBrowser(browser);
}

process.exit(process.exitCode || 0);
