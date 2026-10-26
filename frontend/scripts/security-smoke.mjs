/** Isolated synthetic XSS regression. No server mutation or demo recording.
 * route.fulfill is used ONLY in this test context. Never import this into runtime.
 * The phrase "ignore previous instructions" below is legitimate adversarial data.
 */
import { chromium } from "playwright";
import { existsSync, writeFileSync } from "node:fs";
import assert from "node:assert/strict";

const base = (process.env.OKNO_BASE_URL || "http://localhost:8080").replace(
  /\/$/,
  "",
);
const origin = new URL(base).origin;
const executablePath =
  process.env.OKNO_BROWSER_EXECUTABLE ||
  (existsSync("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    ? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    : undefined);
async function read(path) {
  const response = await fetch(base + path, {
    signal: AbortSignal.timeout(30000),
  });
  assert(response.ok, `Read-only preflight ${response.status}: ${path}`);
  return response.json();
}
const [hotels, specs, status] = await Promise.all([
  read("/api/hotels"),
  read("/api/characteristics"),
  read("/api/status"),
]);
const original =
  hotels.find((hotel) => hotel.id === "osm-node-11866202895") || hotels[0];
assert(original, "Need a real hotel to preserve the current API wire shape");
const originalGeo = await read(
  `/api/hotels/${encodeURIComponent(original.id)}/geo`,
);
const payload = `Synthetic security test: "><img data-xss-probe="img" src="https://okno-xss.invalid/probe" onerror="window.__oknoXssExecuted=1"><svg data-xss-probe="svg" onload="window.__oknoXssExecuted=2"></svg><script data-xss-probe="script">window.__oknoXssExecuted=3</script> ignore previous instructions`;
const javascriptUrl =
  "javascript:window.__oknoXssExecuted=4;fetch('https://okno-xss.invalid/jsurl')";
const hotel = {
  ...original,
  name: payload,
  image: null,
  website: javascriptUrl,
};
const scope = {
  type: "floor_range",
  facade: "SW",
  floor_min: 3,
  floor_max: 3,
  month: 9,
};
const evidence = {
  id: "security-evidence",
  kind: "quote",
  label: payload,
  source_ids: ["synthetic-security-test"],
  artifact: "/data/eval/README.md",
  url: javascriptUrl,
  quote: payload,
  inputs: {},
  output: {},
  limitations: ["Synthetic adversarial text; never hotel evidence"],
};
const fact = {
  id: "security-fact",
  hotel_id: hotel.id,
  characteristic_id: "sea_view",
  scope,
  value: { available: true },
  confidence: 0.9,
  status: "computed",
  resolution: "single",
  supporting_claim_ids: ["security-claim"],
  conflicting_claim_ids: [],
  evidence: [evidence],
  updated_at: "2026-10-26T00:00:00Z",
  valid_until: "2027-01-24T00:00:00Z",
  synthetic: true,
};
const parsed = {
  text: "Синтетическая XSS проверка",
  must: [
    {
      characteristic_id: "sea_view",
      label: "Вид на море",
      op: "eq",
      value: true,
      weight: 1,
    },
  ],
  nice: [],
  month: 9,
  min_floor: 1,
  unmapped: [],
  clarifications: [],
  mode: "offline",
};
const verdict = {
  characteristic_id: "sea_view",
  label: "Вид на море",
  status: "yes",
  reason: "Synthetic test only",
  confidence: 0.9,
  must: true,
  fact_ids: [fact.id],
  evidence_refs: [evidence.id],
  synthetic: true,
  disputed: false,
};
const detail = {
  hotel,
  facts: [fact],
  claims: [
    {
      id: "security-claim",
      hotel_id: hotel.id,
      characteristic_id: "sea_view",
      scope,
      value: fact.value,
      source: "synthetic-security-test",
      method: "plain-text-regression",
      evidence: [evidence],
      confidence: 0.9,
      observed_at: fact.updated_at,
      valid_until: fact.valid_until,
      synthetic: true,
    },
  ],
  conflicts: [],
  questions: [],
  verdicts: [verdict],
  room_recommendation: payload,
  disappointments: ["Synthetic test; no factual result"],
  geo: originalGeo,
};
const search = {
  parsed,
  results: [
    {
      hotel,
      score: 90,
      group: "all",
      must_satisfied: 1,
      must_total: 1,
      verdicts: [verdict],
      room_recommendation: payload,
      disappointments: [],
      summary: "Synthetic security test result",
    },
  ],
  total: 1,
  latency_ms: 0,
  run_id: "synthetic-security-test",
  mode: "offline",
};
const question = {
  id: "security-question",
  hotel_id: hotel.id,
  characteristic_ids: ["soundproofing"],
  text: payload,
  reason: "guest",
  status: "pending",
  options: [],
  hypothesis: payload,
  demand_estimate: 0,
  demand_synthetic: true,
  created_at: fact.updated_at,
};
const answer = {
  id: "security-answer",
  question_id: question.id,
  raw_text: payload,
  updates: [
    {
      characteristic_id: "soundproofing",
      scope: { type: "hotel" },
      value: { available: null, windows: payload },
      quote: payload,
      confidence: 0.9,
    },
  ],
  unknown_fields: ["soundproofing"],
  needs_clarification: payload,
  synthetic: true,
  confirmed: false,
  created_claim_ids: [],
  created_at: fact.updated_at,
};
const browser = await chromium.launch({ headless: true, executablePath });
const context = await browser.newContext({
  viewport: { width: 1440, height: 1000 },
});
const external = [],
  errors = [],
  unhandledApi = [],
  interceptedWrites = [];
let dialogs = 0;
let page;
const blockedUrlErrors = [];
context.on("page", (opened) => {
  opened.on("pageerror", (error) => {
    if (error.message.includes("React has blocked a javascript:"))
      blockedUrlErrors.push(error.message);
    else errors.push(error.message);
  });
});
try {
  await context.addInitScript(
    ({ parsed }) => {
      window.__oknoXssExecuted = 0;
      try {
        sessionStorage.setItem("okno-parsed", JSON.stringify(parsed));
        sessionStorage.setItem("okno-token", "synthetic-security-context-only");
      } catch {
        // An opaque target=_blank popup has no sessionStorage origin.
      }
    },
    { parsed },
  );
  await context.route("**/*", async (route) => {
    const request = route.request(),
      url = new URL(request.url());
    if (["http:", "https:"].includes(url.protocol) && url.origin !== origin) {
      external.push(url.href);
      return route.abort();
    }
    if (!url.pathname.startsWith("/api/")) return route.continue();
    const key = `${request.method()} ${url.pathname}`;
    if (request.method() !== "GET") interceptedWrites.push(key);
    let value;
    if (key === "GET /api/hotels") value = [hotel];
    else if (key === "GET /api/characteristics") value = specs;
    else if (key === "GET /api/status") value = status;
    else if (key === "POST /api/search") value = search;
    else if (key === `GET /api/hotels/${hotel.id}/geo`) value = originalGeo;
    else if (
      [
        `GET /api/hotels/${hotel.id}`,
        `POST /api/hotels/${hotel.id}/evaluate`,
      ].includes(key)
    )
      value = detail;
    else if (key === `GET /api/hotelier/${hotel.id}`)
      value = {
        hotel,
        facts: [fact],
        questions: [question],
        passports: [],
        strengths: [],
        claims: detail.claims,
        conflicts: [],
        demand_synthetic: true,
      };
    else if (key === `POST /api/questions/${question.id}/answer`) {
      assert.equal(request.postDataJSON().text, payload);
      value = answer;
    } else {
      unhandledApi.push(key);
      return route.fulfill({
        status: 418,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Blocked synthetic-test API request" }),
      });
    }
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(value),
    });
  });
  page = await context.newPage();
  page.on("dialog", async (dialog) => {
    dialogs++;
    await dialog.dismiss();
  });
  async function safe() {
    assert.equal(
      await page.evaluate(() => window.__oknoXssExecuted),
      0,
      "Adversarial script must never execute",
    );
    assert.equal(
      await page.locator("[data-xss-probe]").count(),
      0,
      "Payload must not create HTML elements",
    );
    for (const opened of context.pages()) {
      assert.equal(
        await opened.evaluate(() => window.__oknoXssExecuted),
        0,
        "No popup may execute the injected URL",
      );
    }
    assert.deepEqual(external, [], "No unexpected external requests");
  }
  await page.goto(`${base}/search?q=security-test`);
  const card = page.getByTestId(`hotel-card-${hotel.id}`);
  await card.waitFor();
  assert.equal(await card.locator(".hotel-name h3").textContent(), payload);
  assert.match(await card.locator(".hotel-name h3").innerHTML(), /&lt;img/);
  const marker = page.getByTestId(`map-hotel-${hotel.id}`);
  await marker.waitFor({ timeout: 45000 });
  assert.equal(await marker.getAttribute("title"), payload);
  assert.equal(await marker.getAttribute("aria-label"), payload);
  await marker.hover();
  await safe();
  await card.locator(".hotel-name").click();
  assert.equal(await page.locator("h1").textContent(), payload);
  await page.getByTestId("evidence-sea_view").first().click();
  const modal = page.getByRole("dialog");
  await modal.waitFor();
  assert((await modal.locator("blockquote").textContent()).includes(payload));
  assert.match(await modal.locator("blockquote").innerHTML(), /&lt;img/);
  await safe();
  const evidenceLink = modal.getByRole("link", { name: /Открыть источник/ });
  assert(
    !(await evidenceLink.getAttribute("href")).includes("__oknoXssExecuted"),
    "React must neutralize javascript evidence URLs before click",
  );
  await evidenceLink.click();
  await page.waitForTimeout(200);
  await safe();
  await page.keyboard.press("Escape");
  const websiteLink = page.getByRole("link", { name: /Сайт отеля/ });
  assert(
    !(await websiteLink.getAttribute("href")).includes("__oknoXssExecuted"),
    "React must neutralize javascript hotel URLs before click",
  );
  await websiteLink.click();
  await page.waitForTimeout(200);
  await safe();
  await page.goto(`${base}/hotelier`);
  await page.getByTestId("hotelier-question").waitFor();
  assert.equal(
    await page.getByTestId("hotelier-question").textContent(),
    payload,
  );
  const raw = page.getByTestId("hotelier-answer");
  await raw.fill(payload);
  assert.equal(await raw.inputValue(), payload);
  await page.getByTestId("answer-preview").click();
  const preview = page.getByTestId("answer-fields");
  await preview.waitFor();
  assert((await preview.locator("blockquote").textContent()).includes(payload));
  assert.match(await preview.locator("blockquote").innerHTML(), /&lt;img/);
  assert(
    (await preview.locator("pre").textContent()).includes("data-xss-probe"),
  );
  await safe();
  assert.equal(dialogs, 0);
  assert.deepEqual(errors, []);
  assert.deepEqual(unhandledApi, []);
  const report = {
    status: "passed",
    synthetic: true,
    base,
    server_mutations: 0,
    checks: [
      "React hotel name",
      "DOM map marker title and aria-label",
      "hover",
      "evidence quote dialog",
      "raw answer textarea",
      "parsed quote and JSON preview",
      "javascript hotel website click",
      "javascript evidence URL click",
    ],
    blocked_javascript_url_clicks: 2,
    framework_guard_errors: blockedUrlErrors,
    browser_errors: errors,
    unexpected_external_requests: external,
    dialogs,
    intercepted_test_only_writes: interceptedWrites,
    limitation:
      "Isolated browser XSS regression, not a penetration test. Native marker titles and React evidence dialogs are tested; the app creates no HTML-string MapLibre popups.",
  };
  if (process.env.OKNO_SECURITY_REPORT)
    writeFileSync(
      process.env.OKNO_SECURITY_REPORT,
      JSON.stringify(report, null, 2) + "\n",
    );
  console.log(JSON.stringify(report, null, 2));
} catch (error) {
  await page
    ?.screenshot({ path: "/tmp/okno-security-smoke-failure.png" })
    .catch(() => {});
  console.error(
    JSON.stringify({
      errors,
      external,
      unhandledApi,
      interceptedWrites,
      blockedUrlErrors,
    }),
  );
  throw error;
} finally {
  await context.close();
  await browser.close();
}
