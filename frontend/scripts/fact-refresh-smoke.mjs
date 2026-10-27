import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { mkdir, readFile } from "node:fs/promises";
import { build } from "esbuild";
import { chromium } from "playwright";

// All API responses and writes are browser fixtures. No production API is used.
const base = process.env.OKNO_BASE_URL || "http://localhost:5175";
const chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const executablePath =
  process.env.OKNO_BROWSER_EXECUTABLE ||
  (existsSync(chrome) ? chrome : undefined);
const artifacts = new URL(
  "../../runtime/night/fact-refresh-smoke/",
  import.meta.url,
);
await mkdir(artifacts, { recursive: true });
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const hotel = {
  id: "refresh-fixture",
  name: "Отель браузерной проверки",
  lat: 43.58,
  lon: 39.72,
  address: "Синтетическая browser fixture",
  stars: null,
  price_rub: null,
  basic_fields_source: "fixture",
  source_ids: [],
  floors: 3,
  footprint: null,
  image: null,
  image_attribution: null,
  website: null,
  room_types: [],
  tags: {},
};
const query = {
  text: "Тихо ночью; даты сохраняются",
  month: 9,
  facade: null,
  min_floor: null,
  check_in: "2027-09-10",
  check_out: "2027-09-15",
  persona: null,
  must: [
    {
      characteristic_id: "quiet_night",
      label: "Тихо ночью",
      field: "night_db_max",
      op: "lte",
      value: 55,
      weight: 1,
      scope: null,
    },
  ],
  nice: [],
  no_data: [
    {
      characteristic_id: "connectivity",
      label: "Связь: данных нет",
      field: "available",
      op: "eq",
      value: true,
      weight: 1,
      scope: null,
    },
  ],
  no_data_must: [
    {
      characteristic_id: "connectivity",
      label: "Связь: данных нет",
      field: "available",
      op: "eq",
      value: true,
      weight: 1,
      scope: null,
    },
  ],
  unmapped: [],
  clarifications: [],
  mode: "offline",
};
const collection = { type: "FeatureCollection", features: [] };
const geo = {
  facades: ["E", "W"].map((code) => ({
    code,
    sea_probability_by_floor: [0, 0, 0],
  })),
  floors: { count: 3, modeled_count: 3 },
  view_cones: collection,
  route: collection,
  nearby: collection,
};
const scope = { type: "floor_range", facade: "W", floor_min: 1, floor_max: 3 };
function fact(version) {
  return {
    id: `noise-${version}`,
    hotel_id: hotel.id,
    characteristic_id: "quiet_night",
    scope,
    value: {
      available: true,
      index: "quiet",
      night_db_min: 40 + version,
      night_db_max: 45 + version,
    },
    confidence: 0.85,
    status: "confirmed",
    resolution: "Browser fixture",
    supporting_claim_ids: [`claim-${version}`],
    conflicting_claim_ids: [],
    evidence: [],
    updated_at: `2026-10-27T00:00:0${version}Z`,
    valid_until: "2028-01-01T00:00:00Z",
    synthetic: true,
  };
}
function evidence(version) {
  const current = fact(version);
  const item = {
    id: `evidence-${version}`,
    kind: "simulation",
    label: "Browser fixture",
    source_ids: ["browser-fixture"],
    quote: `Основание fixture ${version}`,
    inputs: {},
    output: current.value,
    limitations: ["Только тест интерфейса"],
  };
  return {
    fact: { ...current, evidence: [item] },
    claims: [
      {
        id: `claim-${version}`,
        hotel_id: hotel.id,
        characteristic_id: "quiet_night",
        scope,
        value: current.value,
        source: "browser-fixture",
        method: "fixture",
        evidence: [item],
        confidence: 0.85,
        observed_at: current.updated_at,
        valid_until: current.valid_until,
        synthetic: true,
      },
    ],
  };
}
function detail(version) {
  const current = fact(version);
  return {
    hotel,
    facts: [current],
    claims: [],
    conflicts: [],
    questions: [],
    geo,
    room_recommendation: "Выбор комнаты требует подтверждения отеля.",
    disappointments: [],
    evidence_deferred: true,
    verdicts: [
      {
        characteristic_id: "quiet_night",
        label: "Тихо ночью",
        required: true,
        status: "yes",
        symbol: "✓",
        text: `Вердикт fixture ${version}`,
        confidence: 0.85,
        fact_ids: [current.id],
        evidence_refs: [`evidence-${version}`],
        synthetic: true,
      },
    ],
  };
}
const question = {
  id: "question-fixture",
  hotel_id: hotel.id,
  characteristic_ids: ["quiet_night"],
  text: "Браузерная проверка ответа",
  hypothesis: null,
  options: [],
  priority: 1,
  reason: "guest",
  status: "pending",
  demand_estimate: 0,
  demand_synthetic: false,
  asked_at: "2026-10-27T00:00:00Z",
};
const answer = {
  id: "answer-fixture",
  question_id: question.id,
  raw_text: "Fixture only",
  updates: [
    {
      characteristic_id: "quiet_night",
      scope,
      value: fact(2).value,
      confidence: 0.85,
      quote: "Fixture only",
    },
  ],
  unknown_fields: [],
  needs_clarification: null,
  parser: "offline",
  confirmed: false,
  synthetic: true,
  created_claim_ids: [],
};
const errors = [],
  requests = [],
  checks = [];
const browser = await chromium.launch({ executablePath, headless: true });
const watch = (page) =>
  page.on("pageerror", (error) => errors.push(error.message));
try {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
  });
  let version = 0,
    revision = 0,
    instance = "fixture-instance-a";
  let held = null,
    holdNext = false;
  const evaluated = [],
    related = [];
  await context.route("**/data/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith(".geojson"))
      return route.fulfill({ json: collection });
    if (url.pathname.endsWith(".json")) return route.fulfill({ json: {} });
    const file = new URL(
      `../..${decodeURIComponent(url.pathname)}`,
      import.meta.url,
    );
    const bytes = await readFile(file);
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
    requests.push({ path, method: req.method() });
    if (path.endsWith("/room-advice"))
      return route.fulfill({
        json: [
          {
            id: "refresh-advice",
            characteristic_id: "quiet_night",
            title: "Шум ночью",
            text: `Описание стороны fixture ${version}`,
            status: "partial",
            synthetic: true,
            caveats: ["Только browser fixture"],
            fact_ids: [fact(version).id],
            evidence_refs: [`evidence-${version}`],
            options: [
              {
                scope: { facade: "W", floor_min: 1, floor_max: 3, month: 9 },
                fact_ids: [fact(version).id],
                evidence_refs: [`evidence-${version}`],
                status: "partial",
                synthetic: true,
              },
            ],
          },
        ],
      });
    if (path.endsWith("/rooms"))
      return route.fulfill({
        json: [
          {
            id: "refresh-position",
            scope: { facade: "W", floor_min: 1, floor_max: 3, month: 9 },
            title: "Синтетическое положение",
            description: `Описание положения fixture ${version}`,
            conditional: true,
            caveats: ["Только browser fixture"],
            clauses: [
              {
                characteristic_id: "quiet_night",
                text: `Шум fixture ${version}`,
                fact_ids: [fact(version).id],
                evidence_refs: [`evidence-${version}`],
                status: "partial",
                synthetic: true,
              },
            ],
          },
        ],
      });
    if (path === "/api/revision")
      return route.fulfill({ json: { instance, revision } });
    if (path === "/api/status")
      return route.fulfill({
        json: {
          name: "Browser fixture",
          mode: "offline",
          version: "fixture",
          hotel_count: 1,
          characteristic_count: 1,
          seed_version: "fixture",
        },
      });
    if (path === "/api/hotels") return route.fulfill({ json: [hotel] });
    if (path === "/api/characteristics") return route.fulfill({ json: [] });
    if (path.endsWith("/geo")) return route.fulfill({ json: geo });
    if (path.endsWith("/evidence")) {
      const id = path.match(/facts\/noise-(\d+)\/evidence/);
      return route.fulfill({ json: evidence(Number(id[1])) });
    }
    if (path === `/api/hotels/${hotel.id}/evaluate`) {
      const body = req.postDataJSON();
      evaluated.push(body);
      if (holdNext && body.facade === null) {
        holdNext = false;
        held = { route, response: detail(version) };
        return;
      }
      return route.fulfill({ json: detail(version) });
    }
    if (path === "/api/search") {
      const body = req.postDataJSON();
      related.push(body);
      return route.fulfill({
        json: {
          parsed: query,
          results: [
            {
              hotel: {
                ...hotel,
                id: "related-fixture",
                name: "Другой профиль",
              },
              similarity_reasons: [`Совпадает: профиль fixture ${version}`],
            },
          ],
          total: 1,
          latency_ms: 1,
          run_id: null,
          mode: "offline",
        },
      });
    }
    if (path === `/api/hotelier/${hotel.id}`)
      return route.fulfill({
        json: {
          ...detail(version),
          questions: [question],
          passports: [],
          strengths: [],
        },
      });
    if (path.endsWith("/answer")) return route.fulfill({ json: answer });
    if (path.endsWith("/confirm")) {
      version = 2; // Keep server token unchanged to isolate the storage signal.
      return route.fulfill({ json: { ...answer, confirmed: true } });
    }
    throw new Error(`Unexpected fixture API: ${req.method()} ${path}`);
  });
  const hotelPage = await context.newPage();
  watch(hotelPage);
  await hotelPage.addInitScript((value) => {
    sessionStorage.setItem("okno-parsed", JSON.stringify(value));
  }, query);
  await hotelPage.goto(
    `${base}/hotel/${hotel.id}?room_side=W&room_floor=2&room_month=6`,
  );
  const model = hotelPage.getByTestId("room-lab-fact-quiet_night");
  await model.waitFor();
  await hotelPage
    .getByTestId("room-lab-verdicts")
    .getByText("Вердикт fixture 0")
    .waitFor();
  await hotelPage.getByTestId("room-lab-evidence-quiet_night").click();
  await hotelPage
    .getByRole("dialog")
    .getByText("«Основание fixture 0»")
    .waitFor();
  await hotelPage.screenshot({
    path: new URL("before.png", artifacts).pathname,
    fullPage: true,
  });
  const owner = await context.newPage();
  watch(owner);
  await owner.addInitScript(() => {
    sessionStorage.setItem("okno-token", "browser-fixture-only");
    sessionStorage.setItem(
      "okno-hotelier-session",
      JSON.stringify({ role: "demo_admin", hotel_ids: [] }),
    );
  });
  await owner.goto(`${base}/hotelier`);
  await owner
    .getByTestId("hotelier-answer")
    .fill("Черновик должен сохраниться");
  version = 1;
  revision = 1;
  await hotelPage.waitForFunction(
    () =>
      document
        .querySelector('[data-testid="room-lab-fact-quiet_night"]')
        ?.getAttribute("data-fact-id") === "noise-1",
  );
  await hotelPage
    .getByRole("dialog")
    .getByText("«Основание fixture 1»")
    .waitFor();
  assert.equal(
    await owner.getByTestId("hotelier-answer").inputValue(),
    "Черновик должен сохраниться",
  );
  checks.push(
    "server revision refreshes open Hotel, RoomLab, evidence and preserves another tab's draft",
  );
  await owner.getByTestId("answer-preview").click();
  await owner.getByTestId("answer-confirm").waitFor();
  const confirmedAt = Date.now();
  await owner.getByTestId("answer-confirm").click();
  await hotelPage
    .getByRole("dialog")
    .getByText("«Основание fixture 2»")
    .waitFor({ timeout: 2500 });
  assert.ok(
    Date.now() - confirmedAt < 2500,
    "storage notification should beat the 3s poll",
  );
  assert.match(
    await owner.evaluate(() => localStorage.getItem("okno-data-revision")),
    /^[\da-f]{8}-[\da-f-]{27}$/,
  );
  checks.push(
    "successful confirmation broadcasts only an opaque token and refreshes the other tab immediately",
  );
  await owner
    .getByTestId("hotelier-answer")
    .fill("Новый несохранённый черновик");
  const sameTokenCount = evaluated.length;
  await pause(3400);
  assert.equal(
    evaluated.length,
    sameTokenCount,
    "unchanged server token/search must not cause a refresh loop",
  );
  checks.push("unchanged token and related POST search cause no refresh loop");
  instance = "fixture-instance-b";
  revision = 0;
  version = 3;
  await hotelPage
    .getByRole("dialog")
    .getByText("«Основание fixture 3»")
    .waitFor();
  checks.push(
    "new server instance invalidates even when its numeric revision decreases",
  );
  version = 4;
  revision = 4;
  holdNext = true;
  const holdDeadline = Date.now() + 15000;
  while (!held && Date.now() < holdDeadline) await pause(100);
  assert.ok(held, "old generation request must arrive before the deadline");
  version = 5;
  revision = 5;
  await hotelPage
    .getByRole("dialog")
    .getByText("«Основание fixture 5»")
    .waitFor();
  await held.route.fulfill({ json: held.response });
  await pause(150);
  assert.equal(await model.getAttribute("data-fact-id"), "noise-5");
  assert.match(
    await hotelPage.getByTestId("room-lab-verdicts").innerText(),
    /Вердикт fixture 5/,
  );
  assert.match(
    await hotelPage.getByTestId("fact-card-quiet_night").innerText(),
    /50/,
  );
  assert.match(
    await hotelPage.getByTestId("related-hotels").innerText(),
    /профиль fixture 5/,
  );
  assert.equal(
    await hotelPage.getByTestId("room-lab-facade").inputValue(),
    "W",
  );
  assert.equal(await hotelPage.getByTestId("room-lab-floor").inputValue(), "2");
  assert.equal(await hotelPage.getByTestId("room-lab-month").inputValue(), "6");
  assert.equal(
    await owner.getByTestId("hotelier-answer").inputValue(),
    "Новый несохранённый черновик",
  );
  assert.deepEqual(
    await hotelPage.evaluate(() =>
      JSON.parse(sessionStorage.getItem("okno-parsed")),
    ),
    query,
  );
  assert.ok(
    evaluated.some(
      (body) =>
        body.facade === "W" &&
        body.month === 6 &&
        body.must[0].scope.floor_min === 2,
    ),
  );
  for (const body of evaluated) {
    assert.equal(body.check_in, query.check_in);
    assert.equal(body.check_out, query.check_out);
    assert.equal(body.must[0].value, query.must[0].value);
    assert.deepEqual(body.no_data, query.no_data);
    assert.deepEqual(body.no_data_must, query.no_data_must);
  }
  related.forEach((body) =>
    assert.deepEqual(body, { similar_to: hotel.id, text: "", limit: 3 }),
  );
  checks.push(
    "late old-generation response cannot replace newer facts, verdicts or evidence; slice, dates and reference persist",
  );
  await hotelPage.screenshot({
    path: new URL("after-confirmation.png", artifacts).pathname,
    fullPage: true,
  });
  await hotelPage.getByRole("button", { name: "Закрыть" }).click();
  const position = hotelPage.getByTestId("room-advice-card").first();
  await position
    .getByTestId("room-advice-computed")
    .locator(":scope > summary")
    .click();
  await position.getByTestId("room-advice-evidence").first().click();
  await hotelPage
    .getByRole("dialog")
    .getByText("«Основание fixture 5»")
    .waitFor();
  version = 6;
  revision = 6;
  await hotelPage
    .getByRole("dialog")
    .getByText("«Основание fixture 6»")
    .waitFor();
  await position
    .getByText("Описание стороны fixture 6", { exact: true })
    .first()
    .waitFor();
  checks.push(
    "room descriptions and their open lazy proof follow replacement fact IDs on revision",
  );
  await hotelPage.getByRole("button", { name: "Закрыть" }).click();
  await hotelPage.setViewportSize({ width: 390, height: 844 });
  await hotelPage.getByTestId("room-lab").scrollIntoViewIfNeeded();
  assert.equal(
    await hotelPage.evaluate(() => document.documentElement.scrollWidth),
    390,
  );
  await hotelPage.screenshot({
    path: new URL("mobile.png", artifacts).pathname,
  });
  await hotelPage.getByTestId("evidence-quiet_night").first().click();
  await hotelPage
    .getByRole("dialog")
    .getByText("«Основание fixture 6»")
    .waitFor();
  version = 7;
  revision = 7;
  await hotelPage
    .getByRole("dialog")
    .getByText("«Основание fixture 7»")
    .waitFor();
  checks.push(
    "an already open fact-card EvidenceButton also receives the new value and lazy evidence",
  );
  await context.close();

  // Compile the real hook/API source into a fixture-only mount/unmount harness.
  // This tests lifecycle and write paths without adding any product test UI.
  const bundled = await build({
    stdin: {
      resolveDir: new URL("..", import.meta.url).pathname,
      contents: `import React, {useState} from 'react';
      import {createRoot} from 'react-dom/client';
      import {api, request, useResource} from './src/api.ts';
      const root = createRoot(document.getElementById('root'));
      function Resource({n}) { const value = useResource(() => request('/api/test-resource'));
        return React.createElement('p', {'data-resource':n}, JSON.stringify(value.data)); }
      function App() { const [count,setCount]=useState(3); window.resourceCount=setCount;
        return React.createElement('div',null,Array.from({length:count},(_,n)=>React.createElement(Resource,{key:n,n}))); }
      window.refreshHarness={api,unmount:()=>root.unmount()};
      root.render(React.createElement(App));`,
      loader: "jsx",
    },
    bundle: true,
    write: false,
    format: "esm",
    define: { "process.env.NODE_ENV": '"production"' },
  });
  const openHarness = async (unsupported = false) => {
    const ctx = await browser.newContext();
    const page = await ctx.newPage();
    watch(page);
    const state = {
      polls: 0,
      loads: 0,
      active: 0,
      maxActive: 0,
      writes: 0,
      failWrite: false,
      delay: 350,
    };
    await page.route("**/__fact_refresh_harness", (route) =>
      route.fulfill({
        contentType: "text/html",
        body: '<div id="root"></div>',
      }),
    );
    await page.route("**/api/**", async (route) => {
      const req = route.request(),
        path = new URL(req.url()).pathname;
      if (path === "/api/revision") {
        state.polls++;
        state.maxActive = Math.max(state.maxActive, ++state.active);
        await pause(state.delay);
        state.active--;
        return route.fulfill(
          unsupported
            ? { status: 404, json: { detail: "Fixture: revision unsupported" } }
            : { json: { instance: "harness", revision: 0 } },
        );
      }
      if (path === "/api/test-resource")
        return route.fulfill({ json: { load: ++state.loads } });
      if (state.failWrite && req.method() === "POST")
        return route.fulfill({
          status: 409,
          json: { detail: "Fixture rejected write" },
        });
      if (req.method() === "POST") state.writes++;
      if (path === "/api/runs" || path.startsWith("/api/runs/"))
        return route.fulfill({
          json: {
            id: "run-fixture",
            kind: "collect",
            status: req.method() === "POST" ? "running" : "completed",
          },
        });
      if (path.endsWith("/publish"))
        return route.fulfill({
          json: { run_id: "backfill-fixture", status: "running" },
        });
      return route.fulfill({ json: {} });
    });
    await page.goto(`${base}/__fact_refresh_harness`);
    await page.addScriptTag({
      type: "module",
      content: bundled.outputFiles[0].text,
    });
    await page.waitForFunction(
      () => document.querySelectorAll("[data-resource]").length === 3,
    );
    await page.waitForFunction(() =>
      [...document.querySelectorAll("[data-resource]")].every(
        (node) => node.textContent !== "null",
      ),
    );
    return { ctx, page, state };
  };
  const harness = await openHarness();
  await pause(4100);
  assert.equal(harness.state.polls, 2, "three resources share one poller");
  assert.equal(harness.state.maxActive, 1);
  harness.state.delay = 3500;
  await pause(8100);
  assert.equal(
    harness.state.polls,
    3,
    "next poll waits for a slow previous response to finish",
  );
  assert.equal(harness.state.maxActive, 1);
  harness.state.delay = 350;
  const loadsBeforeReads = harness.state.loads;
  await harness.page.evaluate(async () => {
    const { api } = window.refreshHarness;
    await api.search({ text: "Fixture only" });
    await api.hotel("fixture", {});
    await api.compile("Fixture only");
    await api.answer("fixture", "Fixture only");
  });
  await pause(100);
  assert.equal(harness.state.loads, loadsBeforeReads);
  const writes = [
    () => window.refreshHarness.api.confirmAnswer("fixture"),
    () => window.refreshHarness.api.publish({}),
    () => window.refreshHarness.api.simulateChange(),
    () => window.refreshHarness.api.refresh(),
  ];
  for (const action of writes) {
    const before = harness.state.loads;
    await harness.page.evaluate(action);
    await harness.page.waitForFunction(() =>
      [...document.querySelectorAll("[data-resource]")].every(
        (node) => node.textContent !== "null",
      ),
    );
    await pause(100);
    assert.equal(harness.state.loads, before + 3);
  }
  harness.state.failWrite = true;
  const beforeFailure = harness.state.loads;
  await harness.page.evaluate(() =>
    window.refreshHarness.api.confirmAnswer("fixture").catch(() => null),
  );
  await pause(100);
  assert.equal(harness.state.loads, beforeFailure);
  harness.state.failWrite = false;
  const beforeRun = harness.state.loads;
  await harness.page.evaluate(async () => {
    const { api } = window.refreshHarness;
    await api.startRun("fixture");
    await api.run("run-fixture");
    await api.run("run-fixture");
  });
  await pause(100);
  assert.equal(
    harness.state.loads,
    beforeRun + 3,
    "run completion signals only once",
  );
  checks.push(
    "single nonoverlapping poller; exact write allowlist; failures and read-only POSTs do not invalidate; completed collect signals once",
  );
  await pause(500);
  await harness.page.evaluate(() => {
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "hidden",
    });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  const hiddenPolls = harness.state.polls;
  await pause(3400);
  assert.equal(harness.state.polls, hiddenPolls);
  await harness.page.evaluate(() => {
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "visible",
    });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await pause(450);
  assert.equal(harness.state.polls, hiddenPolls + 1);
  await harness.page.evaluate(() => window.resourceCount(0));
  await pause(100);
  const unmountedPolls = harness.state.polls;
  await pause(3400);
  assert.equal(harness.state.polls, unmountedPolls);
  checks.push(
    "hidden tabs pause polling, visibility resumes it, last unmount removes timers",
  );
  await harness.ctx.close();
  const unavailable = await openHarness(true);
  await pause(6600);
  assert.equal(
    unavailable.state.polls,
    1,
    "unsupported endpoint does not retry forever",
  );
  const beforeLocal = unavailable.state.loads;
  await unavailable.page.evaluate(() =>
    window.refreshHarness.api.confirmAnswer("fixture"),
  );
  await pause(100);
  assert.equal(unavailable.state.loads, beforeLocal + 3);
  checks.push(
    "missing revision endpoint quietly falls back to local notifications without repeated requests",
  );
  await unavailable.ctx.close();
  assert.deepEqual(errors, []);
  console.log(
    JSON.stringify(
      {
        fixture_only: true,
        browser: executablePath,
        checks,
        errors,
        api_requests: requests.length,
        scoped_evaluations: evaluated.filter((body) => body.facade).length,
        artifacts: artifacts.pathname,
      },
      null,
      2,
    ),
  );
} finally {
  await browser.close();
}
