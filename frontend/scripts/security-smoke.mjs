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
if (process.env.OKNO_SECURITY_REPORT) {
  writeFileSync(
    process.env.OKNO_SECURITY_REPORT,
    JSON.stringify({ status: "running", base }) + "\n",
  );
}
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
let xssReport;
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
  xssReport = report;
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

/** Real same-origin auth smoke. Only login/logout POSTs and cabinet GETs.
 * The cabinet's existing idempotent pending-question generation is explicitly
 * permitted for this run. No answers, claims, passports/actions, collect,
 * Studio, simulate or refresh operations are requested.
 */
async function liveAuthSmoke() {
  const browser = await chromium.launch({ headless: true, executablePath });
  const contexts = [];
  const blockedMutations = [],
    liveExternal = [],
    liveErrors = [],
    cabinets = [];
  const evidence = {
    roles: {},
    status_checks: [],
    streams: [],
    quality: {},
    changes: {},
  };
  async function context() {
    const value = await browser.newContext({
      viewport: { width: 1440, height: 1000 },
    });
    contexts.push(value);
    await value.route("**/*", (route) => {
      const req = route.request(),
        url = new URL(req.url());
      if (["http:", "https:"].includes(url.protocol) && url.origin !== origin) {
        liveExternal.push(url.href);
        return route.abort();
      }
      if (
        !["GET", "HEAD", "OPTIONS"].includes(req.method()) &&
        !["/api/hotelier/login", "/api/hotelier/logout"].includes(url.pathname)
      ) {
        blockedMutations.push(`${req.method()} ${url.pathname}`);
        return route.abort();
      }
      return route.continue();
    });
    value.on("page", (opened) => {
      opened.on("pageerror", (error) => liveErrors.push(error.message));
      opened.on("response", (response) => {
        if (
          new URL(response.url()).pathname.startsWith("/api/hotelier/") &&
          response.request().method() === "GET"
        )
          cabinets.push(response.status());
      });
    });
    return value;
  }
  async function statusCode(client, path, expected, token) {
    const result = await client.request.get(base + path, {
      headers: token ? { Authorization: "Bearer " + token } : {},
      timeout: 30000,
    });
    assert.equal(result.status(), expected, `${path} expected ${expected}`);
    evidence.status_checks.push({
      route: path.replace(/\/api\/runs\/[^/]+/, "/api/runs/<private-id>"),
      expected,
      actual: result.status(),
    });
    return result;
  }
  async function login(client, username, password, role, hotelId) {
    const opened = await client.newPage();
    await opened.goto(base + "/hotelier");
    await opened.getByTestId("hotelier-username").fill(username);
    await opened.getByTestId("hotelier-password").fill(password);
    const pending = opened.waitForResponse(
      (r) =>
        new URL(r.url()).pathname === "/api/hotelier/login" &&
        r.request().method() === "POST",
    );
    await opened.getByTestId("hotelier-login").click();
    const response = await pending;
    assert.equal(response.status(), 200);
    const auth = await response.json();
    assert.equal(auth.role, role);
    assert.equal(auth.synthetic, true);
    assert.deepEqual(auth.hotel_ids, hotelId ? [hotelId] : []);
    const selector = opened.getByTestId("hotelier-hotel");
    await selector.waitFor({ timeout: 45000 });
    await opened.locator(".hotelier-summary h3").waitFor({ timeout: 45000 });
    assert.equal(
      await selector.locator("option").count(),
      hotelId ? 1 : status.hotel_count,
    );
    if (hotelId) assert.equal(await selector.inputValue(), hotelId);
    const cookie = (await client.cookies(base + "/api/runs")).find(
      (c) => c.name === "okno_run_session",
    );
    assert(
      cookie?.httpOnly &&
        cookie.sameSite === "Strict" &&
        cookie.path === "/api/runs",
    );
    assert(
      !(await opened.evaluate(() => document.cookie)).includes(
        "okno_run_session=",
      ),
    );
    evidence.roles[username] = {
      role,
      selector_options: await selector.locator("option").count(),
      own_hotel_selected: hotelId
        ? (await selector.inputValue()) === hotelId
        : null,
      cookie: {
        httpOnly: cookie.httpOnly,
        sameSite: cookie.sameSite,
        path: cookie.path,
      },
    };
    return { page: opened, token: auth.token };
  }
  async function stream(opened, runId, who) {
    const result = await opened.evaluate(
      (path) =>
        new Promise((resolve, reject) => {
          const source = new EventSource(path);
          let steps = 0;
          const timer = setTimeout(() => {
            source.close();
            reject(new Error("Private SSE timed out"));
          }, 20000);
          source.addEventListener("step", () => {
            steps++;
          });
          source.addEventListener("done", (event) => {
            clearTimeout(timer);
            source.close();
            resolve({ steps, status: JSON.parse(event.data).status });
          });
          source.onerror = () => {
            clearTimeout(timer);
            source.close();
            reject(new Error("Private SSE rejected its owner cookie"));
          };
        }),
      `/api/runs/${encodeURIComponent(runId)}/stream`,
    );
    assert(result.steps > 0);
    evidence.streams.push({
      principal: who,
      native_EventSource: true,
      steps: result.steps,
      status: result.status,
    });
  }
  async function logout(client, opened) {
    await opened.goto(base + "/hotelier");
    await opened.getByTestId("hotelier-hotel").waitFor({ timeout: 45000 });
    const result = opened.waitForResponse(
      (r) => new URL(r.url()).pathname === "/api/hotelier/logout",
    );
    await opened.getByRole("button", { name: "Выйти", exact: true }).click();
    assert.equal((await result).status(), 200);
    await opened.getByTestId("hotelier-login").waitFor();
    assert.equal(
      await opened.evaluate(() => sessionStorage.getItem("okno-token")),
      null,
    );
    assert.equal(
      await opened.evaluate(() =>
        sessionStorage.getItem("okno-hotelier-session"),
      ),
      null,
    );
    assert(
      !(await client.cookies(base + "/api/runs")).some(
        (c) => c.name === "okno_run_session",
      ),
    );
  }
  try {
    const publicClient = await context();
    const publicPage = await publicClient.newPage();
    await publicPage.goto(base + "/changes");
    await publicPage.getByTestId("changes-auth-prompt").waitFor();
    assert(await publicPage.getByTestId("simulate-change").isDisabled());
    assert(
      await publicPage
        .getByRole("button", { name: /Перепроверить истёкшие TTL/ })
        .isDisabled(),
    );
    assert.equal(await publicPage.locator('a[href*="?passport="]').count(), 0);
    const publicChanges = await (
      await statusCode(publicClient, "/api/changes", 200)
    ).json();
    assert(
      publicChanges.every(
        (event) =>
          event.notifications.length === 0 &&
          Number.isInteger(event.payload.notification_count),
      ),
    );
    const publicText = JSON.stringify(publicChanges);
    assert(
      !publicText.includes('"passport_id"') &&
        !publicText.includes('"item_ids"'),
    );
    await statusCode(publicClient, "/api/passports", 401);
    const a = await context(),
      b = await context(),
      admin = await context();
    const A = "osm-node-11866202895",
      B = "osm-way-201474330";
    const loginA = await login(
      a,
      "cascade",
      "cascade-demo-2026",
      "hotelier",
      A,
    );
    const loginB = await login(b, "brevis", "brevis-demo-2026", "hotelier", B);
    const loginAdmin = await login(
      admin,
      "hotelier",
      "hotelier123",
      "demo_admin",
    );
    await statusCode(a, `/api/hotelier/${B}`, 403, loginA.token);
    await statusCode(b, `/api/hotelier/${A}`, 403, loginB.token);
    for (const [client, auth, own, foreign] of [
      [a, loginA, A, B],
      [b, loginB, B, A],
    ]) {
      const passports = await (
        await statusCode(client, "/api/passports", 200, auth.token)
      ).json();
      assert(passports.every((passport) => passport.hotel_id === own));
      await statusCode(
        client,
        `/api/passports?hotel_id=${foreign}`,
        403,
        auth.token,
      );
    }
    const privateRuns = await (
      await statusCode(admin, "/api/runs", 200, loginAdmin.token)
    ).json();
    const privateA = privateRuns.find(
      (run) => run.kind === "interview" && run.hotel_id === A,
    );
    const privateB = privateRuns.find(
      (run) => run.kind === "interview" && run.hotel_id === B,
    );
    assert(
      privateA && privateB,
      "Existing versioned tenant interview runs must be available; never create test traces in the main DB",
    );
    const publicRuns = await (
      await statusCode(publicClient, "/api/runs", 200)
    ).json();
    assert(publicRuns.every((run) => run.kind !== "interview"));
    for (const run of [privateA, privateB]) {
      await statusCode(publicClient, `/api/runs/${run.id}`, 401);
      await statusCode(publicClient, `/api/runs/${run.id}/stream`, 401);
    }
    await stream(loginA.page, privateA.id, "cascade");
    await stream(loginB.page, privateB.id, "brevis");
    await statusCode(a, `/api/runs/${privateB.id}/stream`, 403, loginA.token);
    await statusCode(b, `/api/runs/${privateA.id}/stream`, 403, loginB.token);
    await statusCode(admin, `/api/runs/${privateB.id}`, 403, loginA.token);
    await statusCode(admin, `/api/runs/${privateB.id}`, 401, "invalid");
    const adminChanges = await (
      await statusCode(admin, "/api/changes", 200, loginAdmin.token)
    ).json();
    const passports = await (
      await statusCode(admin, "/api/passports", 200, loginAdmin.token)
    ).json();
    for (const passport of passports)
      assert(
        !publicText.includes(passport.id),
        "Public Changes must not distribute guest passport capabilities",
      );
    for (const event of publicChanges)
      assert.equal(
        event.payload.notification_count,
        adminChanges.find((value) => value.id === event.id)?.notifications
          .length,
      );
    evidence.changes = {
      public_events: publicChanges.length,
      public_individual_notifications: 0,
      aggregate_notification_count: publicChanges.reduce(
        (n, event) => n + event.payload.notification_count,
        0,
      ),
      admin_visible_notifications: adminChanges.reduce(
        (n, event) => n + event.notifications.length,
        0,
      ),
      passports_checked_for_public_uuid_leak: passports.length,
      guest_mutations_disabled: true,
    };
    await logout(a, loginA.page);
    await statusCode(a, `/api/runs/${privateA.id}/stream`, 401);
    await logout(b, loginB.page);
    await statusCode(b, `/api/runs/${privateB.id}/stream`, 401);
    await logout(admin, loginAdmin.page);
    await statusCode(admin, `/api/runs/${privateB.id}/stream`, 401);
    const qualityPage = await publicClient.newPage();
    const qualityResponse = qualityPage.waitForResponse(
      (r) => new URL(r.url()).pathname === "/api/quality" && r.ok(),
      { timeout: 60000 },
    );
    const start = Date.now();
    await qualityPage.goto(base + "/quality");
    await qualityPage
      .getByTestId("filter-comparison")
      .waitFor({ timeout: 60000 });
    await qualityPage.getByTestId("fact-provenance").waitFor();
    const quality = await (await qualityResponse).json();
    const comparison = quality.filter_comparison,
      provenance = quality.provenance;
    assert.equal(comparison.schema_version, 1);
    assert.equal(comparison.settings.search_min_floor, 1);
    assert.equal(
      provenance.known,
      provenance.non_synthetic + provenance.synthetic,
    );
    assert(provenance.mixed <= provenance.synthetic);
    assert(
      Math.abs(
        provenance.synthetic_share - provenance.synthetic / provenance.known,
      ) < 1e-12,
    );
    assert(
      (await qualityPage.getByTestId("filter-comparison").innerText()).includes(
        "не реальные окна",
      ),
    );
    await qualityPage
      .getByTestId("filter-comparison")
      .locator("summary")
      .click();
    const baselineResponse = await publicClient.request.get(
      base + "/data/eval/filter_baseline.json",
    );
    assert.equal(baselineResponse.status(), 200);
    assert.deepEqual(await baselineResponse.json(), comparison);
    await qualityPage.screenshot({
      path: "/tmp/okno-live-quality-desktop.png",
    });
    await qualityPage.setViewportSize({ width: 390, height: 844 });
    assert.equal(
      await qualityPage.evaluate(() => document.documentElement.scrollWidth),
      390,
      "Quality mobile layout must not overflow",
    );
    await qualityPage.screenshot({ path: "/tmp/okno-live-quality-mobile.png" });
    evidence.quality = {
      ui_check_ms: Date.now() - start,
      hotel_count: quality.hotel_count,
      baseline_within_radius: comparison.baseline.within_radius,
      compatible_supported: comparison.search.compatible_supported,
      outside_radius_supported: comparison.search.outside_radius_supported,
      outside_radius_partial: comparison.search.outside_radius_partial,
      provenance,
      mobile_width: 390,
      baseline_artifact_matches_API: true,
    };
    assert.deepEqual(blockedMutations, []);
    assert.deepEqual(liveExternal, []);
    assert.deepEqual(liveErrors, []);
    return {
      status: "passed",
      base,
      generated_at: new Date().toISOString(),
      ...evidence,
      browser_errors: liveErrors,
      unexpected_external_requests: liveExternal,
      blocked_forbidden_mutations: blockedMutations,
      cabinet_get_responses: cabinets,
      permitted_exception:
        "Real GET cabinet may idempotently materialize pending questions. Login/logout and this existing behavior were explicitly authorized; no other mutations requested.",
      limitations: [
        "Public demo identities verify access boundaries; this is not production IAM.",
        "Logout removes local cookie/token; copied stateless Bearer remains valid until expiry/restart.",
        "Quality comparison is a model on saved seed, not conversion or measured real-window accuracy.",
      ],
    };
  } finally {
    for (const client of contexts) await client.close();
    await browser.close();
  }
}
if (process.argv.includes("--live-auth")) {
  try {
    const live = await liveAuthSmoke();
    const combined = {
      status: "passed",
      command: "node frontend/scripts/security-smoke.mjs --live-auth",
      xss: xssReport,
      live_auth: live,
    };
    if (process.env.OKNO_SECURITY_REPORT)
      writeFileSync(
        process.env.OKNO_SECURITY_REPORT,
        JSON.stringify(combined, null, 2) + "\n",
      );
    console.log(JSON.stringify(combined, null, 2));
  } catch (error) {
    if (process.env.OKNO_SECURITY_REPORT)
      writeFileSync(
        process.env.OKNO_SECURITY_REPORT,
        JSON.stringify(
          {
            status: "failed",
            xss: xssReport,
            live_auth: { status: "failed", error: String(error) },
          },
          null,
          2,
        ) + "\n",
      );
    throw error;
  }
}
