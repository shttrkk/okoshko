import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { mkdir, readFile } from "node:fs/promises";
import { build } from "esbuild";
import { chromium } from "playwright";

// Every API response, write and panorama document is intercepted locally.
const base = process.env.OKNO_BASE_URL || "http://localhost:5175";
async function confirmAsk(page, button) {
  const confirm = page.getByTestId("ask-confirm");
  if (!(await confirm.isVisible())) await button.click();
  await confirm.waitFor();
  await confirm.click();
}

const chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const executablePath =
  process.env.OKNO_BROWSER_EXECUTABLE ||
  (existsSync(chrome) ? chrome : undefined);
const artifacts = new URL(
  "../../runtime/night/scoped-question-smoke/",
  import.meta.url,
);
await mkdir(artifacts, { recursive: true });
const hotel = {
  id: "scoped-question-fixture",
  name: "Отель проверки пожеланий",
  lat: 43.58,
  lon: 39.72,
  address: "Синтетическая browser fixture",
  stars: null,
  price_rub: null,
  basic_fields_source: "fixture",
  source_ids: [],
  floors: 10,
  footprint: null,
  image: null,
  image_attribution: null,
  website: null,
  room_types: [],
  tags: {},
};
const query = {
  text: "Море из окна и аптека рядом",
  month: 9,
  facade: null,
  min_floor: null,
  check_in: "2027-09-12",
  check_out: "2027-09-18",
  must: [
    {
      characteristic_id: "sea_view",
      label: "Море из окна",
      field: "available",
      op: "eq",
      value: true,
      weight: 1,
      scope: null,
    },
    {
      characteristic_id: "night_pharmacy",
      label: "Аптека рядом",
      field: "distance_m",
      op: "lte",
      value: 500,
      weight: 1,
      scope: null,
    },
  ],
  nice: [],
  no_data: [],
  no_data_must: [],
  unmapped: [],
  clarifications: [],
  mode: "offline",
};
const collection = { type: "FeatureCollection", features: [] };
const geo = {
  floors: { count: 10, modeled_count: 10 },
  view_cones: collection,
  route: collection,
  nearby: collection,
  facades: ["E", "W"].map((code) => ({
    code,
    geometry: {
      type: "LineString",
      coordinates: [
        [39.72, 43.58],
        [39.7201, 43.5801],
      ],
    },
    sea_probability_by_floor: Array(10).fill(0),
  })),
};
const factScope = {
  type: "floor_range",
  facade: "W",
  floor_min: 5,
  floor_max: 9,
  month: 9,
};
const sea = {
  id: "sea-fixture",
  hotel_id: hotel.id,
  characteristic_id: "sea_view",
  scope: factScope,
  value: { available: true, probability: 0.5, facade: "W", min_floor: 5 },
  confidence: 0.5,
  status: "stale",
  resolution: "Browser fixture",
  supporting_claim_ids: [],
  conflicting_claim_ids: [],
  evidence: [],
  updated_at: "2026-10-27T00:00:00Z",
  valid_until: "2028-01-01T00:00:00Z",
  synthetic: true,
};
const pharmacy = {
  ...sea,
  id: "pharmacy-fixture",
  characteristic_id: "night_pharmacy",
  scope: { type: "hotel" },
  value: null,
  confidence: 0,
  status: "unknown",
};
const specs = [
  {
    id: "sea_view",
    name_ru: "Вид на море",
    category: "Вид",
    scope: "floor_range",
  },
  {
    id: "night_pharmacy",
    name_ru: "Аптека",
    category: "Рядом",
    scope: "hotel",
  },
  {
    id: "room_orientation",
    name_ru: "Сторона и этаж номера",
    category: "Номер",
    scope: "room_type",
  },
];
const detail = {
  hotel,
  facts: [sea, pharmacy],
  claims: [],
  conflicts: [],
  questions: [],
  geo,
  room_recommendation:
    "Наличие и расположение номера требуют уточнения у отеля.",
  disappointments: [],
  verdicts: query.must.map((criterion) => ({
    characteristic_id: criterion.characteristic_id,
    label: criterion.label,
    required: true,
    status: "unknown",
    symbol: "?",
    text: "Браузерная проверка: требуется уточнение",
    confidence: 0,
    fact_ids: [
      criterion.characteristic_id === "sea_view" ? sea.id : pharmacy.id,
    ],
    evidence_refs: [],
  })),
};
const exactScope = (facade, floor, month = 6) => ({
  type: "floor_range",
  facade,
  floor_min: floor,
  floor_max: floor,
  month,
});
const errors = [],
  requests = [],
  checks = [],
  bodies = [],
  evaluationBodies = [];
const pending = new Map();
let nextError = null,
  held = null,
  holdNext = false;
const questionFor = (body) => {
  const key = `${body.hotel_id}:${body.characteristic_id}`;
  let question = pending.get(key);
  if (!question) {
    question = {
      id: `question-${body.characteristic_id}`,
      hotel_id: body.hotel_id,
      characteristic_ids: [body.characteristic_id],
      text: `Уточните: ${body.characteristic_id}`,
      hypothesis: null,
      options: ["Да", "Нет", "Не знаю"],
      priority: 1,
      reason: "guest",
      status: "pending",
      demand_estimate: 0,
      demand_synthetic: false,
      requested_scopes: [],
      asked_at: "2026-10-27T00:00:00Z",
    };
    pending.set(key, question);
  }
  if (
    body.scope &&
    !question.requested_scopes.some(
      (scope) => JSON.stringify(scope) === JSON.stringify(body.scope),
    )
  )
    question.requested_scopes.push(body.scope);
  return structuredClone(question);
};
const browser = await chromium.launch({ executablePath, headless: true });
try {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
  });
  await context.route("https://yandex.ru/**", (route) =>
    route.fulfill({
      contentType: "text/html",
      body: "<p>Panorama browser fixture</p>",
    }),
  );
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
    const request = route.request(),
      path = new URL(request.url()).pathname;
    requests.push({ path, method: request.method() });
    if (path.endsWith("/room-advice")) return route.fulfill({ json: [] });
    if (path.endsWith("/rooms")) return route.fulfill({ json: [] });
    if (path === "/api/revision")
      return route.fulfill({
        json: { instance: "scoped-question-fixture", revision: 0 },
      });
    if (path === "/api/status")
      return route.fulfill({
        json: {
          name: "Browser fixture",
          mode: "offline",
          version: "fixture",
          hotel_count: 1,
          characteristic_count: 3,
          seed_version: "fixture",
        },
      });
    if (path === "/api/hotels") return route.fulfill({ json: [hotel] });
    if (path === "/api/characteristics") return route.fulfill({ json: specs });
    if (path.endsWith("/geo")) return route.fulfill({ json: geo });
    if (path.endsWith("/evaluate")) {
      evaluationBodies.push(request.postDataJSON());
      return route.fulfill({ json: detail });
    }
    if (path === `/api/hotels/${hotel.id}`)
      return route.fulfill({ json: detail });
    if (path === "/api/search")
      return route.fulfill({
        json: {
          parsed: query,
          results: [],
          total: 0,
          latency_ms: 1,
          run_id: null,
          mode: "offline",
        },
      });
    if (path === `/api/hotelier/${hotel.id}`)
      return route.fulfill({
        json: {
          ...detail,
          questions: [...pending.values()],
          passports: [],
          strengths: [],
        },
      });
    if (path === "/api/questions") {
      const body = request.postDataJSON();
      bodies.push(body);
      if (nextError) {
        const value = nextError;
        nextError = null;
        return route.fulfill({
          status: value.status,
          json: { detail: value.message },
        });
      }
      const value = questionFor(body);
      if (holdNext) {
        holdNext = false;
        held = { route, value };
        return;
      }
      return route.fulfill({ json: value });
    }
    throw new Error(`Unexpected fixture API: ${request.method()} ${path}`);
  });
  const page = await context.newPage();
  page.on("pageerror", (error) => errors.push(error.message));
  await page.addInitScript(
    (value) => sessionStorage.setItem("okno-parsed", JSON.stringify(value)),
    query,
  );
  await page.goto(
    `${base}/hotel/${hotel.id}?room_side=W&room_floor=7&room_month=6`,
  );
  await page.getByTestId("room-lab-verdicts").waitFor();
  const toolbar = page.getByTestId("room-lab-map-context");
  const askWindow = toolbar.getByTestId("ask-room_orientation");
  await confirmAsk(page, askWindow);
  await askWindow.getByText("Вопрос отправлен").waitFor();
  assert.deepEqual(bodies.at(-1), {
    hotel_id: hotel.id,
    characteristic_id: "room_orientation",
    scope: exactScope("W", 7),
  });
  await page
    .getByRole("status")
    .getByText("Вопрос отправлен. Ответ появится здесь и в паспорте ожиданий.", { exact: true })
    .waitFor();
  const choose = async (facade, floor) => {
    await page.getByTestId("room-lab-facade").selectOption(facade);
    await page.getByTestId("room-lab-floor").selectOption(String(floor));
    await page.waitForFunction(
      ({ side, level }) => {
        const node = document.querySelector(
          '[data-testid="room-lab-verdicts"]',
        );
        return (
          node?.getAttribute("data-room-facade") === side &&
          node.getAttribute("data-room-floor") === String(level)
        );
      },
      { side: facade, level: floor },
    );
  };
  await choose("E", 3);
  assert.equal(await askWindow.isEnabled(), true);
  await confirmAsk(page, askWindow);
  await askWindow.getByText("Вопрос отправлен").waitFor();
  assert.deepEqual(bodies.at(-1).scope, exactScope("E", 3));
  const aggregate = pending.get(`${hotel.id}:room_orientation`);
  assert.equal(aggregate.id, "question-room_orientation");
  assert.deepEqual(aggregate.requested_scopes, [
    exactScope("W", 7),
    exactScope("E", 3),
  ]);
  checks.push(
    "W/7/June and E/3/June reach one pending question; changing slice re-enables the same sent button",
  );
  await toolbar.getByRole("button", { name: "Панорама стороны" }).click();
  const footer = page.getByTestId("panorama-question");
  assert.match(await footer.innerText(), /восток.*3-й этаж.*июнь/);
  assert.equal(await footer.getByTestId("ask-room_orientation").isDisabled(), true);
  await footer.getByText("Вопрос отправлен").waitFor();
  assert.deepEqual(bodies.at(-1).scope, exactScope("E", 3));
  assert.equal(aggregate.requested_scopes.length, 2);
  await page.getByRole("button", { name: "Закрыть", exact: true }).click();
  checks.push(
    "panorama footer keeps the chosen window context and states it as a wish, with no external send",
  );
  await choose("W", 7);
  await confirmAsk(page, page.getByTestId("room-lab-verdicts").getByTestId("ask-sea_view"));
  await page
    .getByTestId("room-lab-verdicts")
    .getByText("Вопрос отправлен")
    .waitFor();
  assert.deepEqual(bodies.at(-1).scope, exactScope("W", 7));
  assert.equal(await page.getByTestId("fact-card-sea_view").getByTestId("ask-sea_view").isDisabled(), true);
  await page
    .getByTestId("fact-card-sea_view")
    .getByText("Вопрос отправлен")
    .waitFor();
  assert.deepEqual(bodies.at(-1).scope, exactScope("W", 7));
  await confirmAsk(page, page.getByTestId("room-lab-verdicts").getByTestId("ask-night_pharmacy"));
  await page
    .getByTestId("room-lab-verdicts")
    .getByTestId("ask-night_pharmacy")
    .getByText("Вопрос отправлен")
    .waitFor();
  assert.equal(Object.hasOwn(bodies.at(-1), "scope"), false);
  assert.equal(await page.getByTestId("fact-card-night_pharmacy").getByTestId("ask-night_pharmacy").isDisabled(), true);
  await page
    .getByTestId("fact-card-night_pharmacy")
    .getByText("Вопрос отправлен")
    .waitFor();
  assert.equal(Object.hasOwn(bodies.at(-1), "scope"), false);
  checks.push(
    "RoomLab and fact-card room questions carry exact slice; hotel-wide pharmacy remains unscoped",
  );
  await choose("W", 8);
  nextError = {
    status: 422,
    message: "Недопустимый срез окна (browser fixture)",
  };
  await confirmAsk(page, askWindow);
  await page.getByRole("dialog")
    .getByRole("alert")
    .getByText(
      nextError?.message || "Недопустимый срез окна (browser fixture)",
      { exact: true },
    )
    .waitFor();
  assert.equal(await askWindow.isEnabled(), true);
  nextError = { status: 409, message: "Вопрос изменился (browser fixture)" };
  await confirmAsk(page, askWindow);
  await page.getByRole("dialog")
    .getByRole("alert")
    .getByText("Вопрос изменился (browser fixture)", { exact: true })
    .waitFor();
  assert.equal(await askWindow.isEnabled(), true);
  await confirmAsk(page, askWindow);
  await askWindow.getByText("Вопрос отправлен").waitFor();
  checks.push(
    "422/409 stay visible and retryable, without optimistic confirmation or automatic writes",
  );
  assert.deepEqual(
    await page.evaluate(() =>
      JSON.parse(sessionStorage.getItem("okno-parsed")),
    ),
    query,
  );
  for (const body of evaluationBodies) {
    assert.equal(body.check_in, query.check_in);
    assert.equal(body.check_out, query.check_out);
    assert.deepEqual(
      body.must.map((entry) => [
        entry.characteristic_id,
        entry.field,
        entry.op,
        entry.value,
      ]),
      query.must.map((entry) => [
        entry.characteristic_id,
        entry.field,
        entry.op,
        entry.value,
      ]),
    );
  }
  assert.equal(await page.getByTestId("room-lab-month").inputValue(), "6");
  await page.screenshot({
    path: new URL("selected-window-question.png", artifacts).pathname,
    fullPage: true,
  });
  await page.getByTestId("room-lab").locator("summary").first().click();
  await page.getByTestId("fact-map-sea_view-W").click();
  const selected = page.getByTestId("hotel-map-context");
  await confirmAsk(page, selected.getByTestId("ask-sea_view"));
  await selected.getByText("Вопрос отправлен").waitFor();
  assert.deepEqual(bodies.at(-1).scope, factScope);
  checks.push(
    "without an active slice, selected Fact.scope is preserved; global parsed, dates and inputs remain unchanged",
  );
  const owner = await context.newPage();
  owner.on("pageerror", (error) => errors.push(error.message));
  await owner.addInitScript(() => {
    sessionStorage.setItem("okno-token", "browser-fixture-only");
    sessionStorage.setItem(
      "okno-hotelier-session",
      JSON.stringify({ role: "demo_admin", hotel_ids: [] }),
    );
  });
  await owner.goto(`${base}/hotelier`);
  const requested = owner.getByTestId("hotelier-requested-scopes");
  await requested.waitFor();
  const description = await requested.innerText();
  assert.match(description, /запад.*7-й этаж.*июнь/);
  assert.match(description, /восток.*3-й этаж.*июнь/);
  assert.match(description, /пожелания гостей, а не подтверждение номера/);
  await owner.screenshot({
    path: new URL("requested-scopes-cabinet.png", artifacts).pathname,
    fullPage: true,
  });
  await owner.setViewportSize({ width: 390, height: 844 });
  await requested.scrollIntoViewIfNeeded();
  assert.equal(
    await owner.evaluate(() => document.documentElement.scrollWidth),
    390,
  );
  await owner.screenshot({
    path: new URL("requested-scopes-mobile.png", artifacts).pathname,
  });
  checks.push(
    "cabinet lists aggregated requested scopes readably as unconfirmed wishes; mobile has no overflow",
  );

  // Mount the real AskButton to exercise prop changes and late responses.
  const bundled = await build({
    stdin: {
      resolveDir: new URL("..", import.meta.url).pathname,
      contents: `import React,{useState} from 'react';import {createRoot} from 'react-dom/client';
      import {AskButton} from './src/components/ui.tsx';import {AppProvider} from './src/state.tsx';
      function App(){const [characteristic,setCharacteristic]=useState('sea_view');window.setAskCharacteristic=setCharacteristic;
        return React.createElement(AskButton,{hotelId:'${hotel.id}',characteristicId:characteristic,scope:{type:'floor_range',facade:'W',floor_min:7,floor_max:7,month:6}});}
      createRoot(document.getElementById('root')).render(React.createElement(AppProvider,null,React.createElement(App)));`,
      loader: "jsx",
    },
    bundle: true,
    // This component harness uses the production page's stylesheet.
    loader: { ".css": "empty" },
    write: false,
    format: "esm",
    define: { "process.env.NODE_ENV": '"production"' },
  });
  const harness = await context.newPage();
  harness.on("pageerror", (error) => errors.push(error.message));
  await harness.route("**/__scoped_question_harness", (route) =>
    route.fulfill({ contentType: "text/html", body: '<div id="root"></div>' }),
  );
  await harness.goto(`${base}/__scoped_question_harness`);
  // This independent race case starts without successes from the earlier UI flow.
  await harness.evaluate((hotelId) => {
    for (const key of Object.keys(localStorage)) {
      if (key.startsWith(`okno-question:["${hotelId}",`)) localStorage.removeItem(key);
    }
  }, hotel.id);
  await harness.addScriptTag({
    type: "module",
    content: bundled.outputFiles[0].text,
  });
  holdNext = true;
  await confirmAsk(harness, harness.getByTestId("ask-sea_view"));
  await harness.waitForFunction(
    () => document.querySelector('[data-testid="ask-sea_view"]')?.disabled,
  );
  const heldDeadline = Date.now() + 3000;
  while (!held && Date.now() < heldDeadline)
    await new Promise((resolve) => setTimeout(resolve, 20));
  assert.ok(held);
  await harness.evaluate(() => window.setAskCharacteristic("soundproofing"));
  const changed = harness.getByTestId("ask-soundproofing");
  assert.equal(await changed.isEnabled(), true);
  await confirmAsk(harness, changed);
  await changed.getByText("Вопрос отправлен").waitFor();
  await held.route.fulfill({ json: held.value });
  assert.equal(await changed.isDisabled(), true);
  await harness.evaluate(() => window.setAskCharacteristic("quiet_night"));
  assert.equal(await harness.getByTestId("ask-quiet_night").isEnabled(), true);
  checks.push(
    "characteristic change re-enables AskButton and old async completion cannot mark the new question sent",
  );
  assert.deepEqual(errors, []);
  console.log(
    JSON.stringify(
      {
        fixture_only: true,
        browser: executablePath,
        checks,
        errors,
        api_requests: requests.length,
        question_posts: bodies.length,
        pending_questions: pending.size,
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
