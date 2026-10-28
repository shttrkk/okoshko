import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

// Importing this module performs no requests. Run only after root releases the
// shared test copy: await runPanelR3SearchActions({ base, artifacts }).
// No response fixtures, login, answers, questions, passports or evaluate POSTs.
const ORIGIN = "http://localhost:8081";
export const SEARCH_CASES = Object.freeze({
  pets: "С питомцем",
  hard_no: "вид на море, тихий номер, до пляжа без лестниц",
  wifi: "Нужен быстрый Wi-Fi не меньше 30 Мбит/с",
  masha: "тихий номер с видом на закат недалеко от моря",
  dina_full:
    "Едем втроём с подругами и с собакой (15 кг) на неделю. Хотим жить в центре, чтобы рядом были бары и кафе. Мне нужен быстрый интернет, я работаю удалённо.",
  dina_short: "Втроём с собакой, центр, хороший вайфай",
  center: "центр",
  center_city: "в центре города",
  center_relative: "ближе к центру",
  center_district: "центральный район",
});
const readOnly = new Set(["GET", "HEAD", "OPTIONS"]);
// GET cabinet is intentionally excluded: it can refresh question state.
// No interviewer, answer, passport or other operational API is needed here.
const publicReadApi =
  /^\/api\/(?:revision|status|hotels|characteristics|sources|tools)$|^\/api\/hotels\/[A-Za-z0-9][A-Za-z0-9._-]*(?:\/(?:geo|photos|rooms|room-advice)|\/facts\/[A-Za-z0-9][A-Za-z0-9._-]*\/evidence)?$/;
const normalize = (value) =>
  String(value).toLocaleLowerCase("ru").replace(/\s+/g, " ").trim();
const safeError = (error) =>
  String(error?.message || error)
    .replace(/\bBearer\s+\S+/gi, "Bearer [redacted]")
    .replace(/\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_.-]+/g, "[token redacted]");

export function assertR3SearchRequest(method, target, env = process.env) {
  const url = new URL(String(target));
  assert(
    url.origin === ORIGIN && !url.username && !url.password && !url.hash,
    "Search actions are restricted to the plain http://localhost:8081 test copy",
  );
  assert(
    readOnly.has(method) ||
      (method === "POST" && url.pathname === "/api/search" && !url.search),
    "Only read-only requests and the exact /api/search POST are permitted",
  );
  if (readOnly.has(method) && url.pathname.startsWith("/api/"))
    assert(
      publicReadApi.test(url.pathname),
      "Only explicitly read-only public search APIs are permitted",
    );
  if (!readOnly.has(method))
    assert.equal(
      env.OKNO_PANEL_R3_WRITES,
      "1",
      "Set OKNO_PANEL_R3_WRITES=1 before any search POST",
    );
  return url;
}

export function createR3SearchClient({
  base = ORIGIN,
  env = process.env,
  fetchImpl = globalThis.fetch,
} = {}) {
  const origin = assertR3SearchRequest("GET", base, env);
  assert(
    origin.pathname === "/" && !origin.search,
    "Use a plain test-copy origin",
  );
  const requests = [];
  async function request(method, path, body) {
    assert(
      typeof path === "string" && path.startsWith("/api/"),
      "An API-relative path is required",
    );
    const url = assertR3SearchRequest(method, new URL(path, origin), env);
    assert(url.pathname.startsWith("/api/"), "API path traversal is forbidden");
    const response = await fetchImpl(url, {
      method,
      credentials: "omit",
      redirect: "error",
      signal: AbortSignal.timeout(60000),
      ...(body === undefined
        ? {}
        : {
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          }),
    });
    requests.push({ method, path: url.pathname, status: response.status });
    assert(response.ok, `${method} ${url.pathname}: HTTP ${response.status}`);
    return response.json();
  }
  return {
    read: (path) => request("GET", path),
    search(text, { limit = 250, compact = false } = {}) {
      assert(
        typeof text === "string" && text.trim() && text.length <= 2000,
        "A bounded search text is required",
      );
      assert(
        Number.isInteger(limit) && limit >= 1 && limit <= 250,
        "Search limit must be 1–250",
      );
      return request("POST", "/api/search", { text, limit, compact });
    },
    get requests() {
      return requests.slice();
    },
  };
}

const verdicts = (hit) => [
  ...hit.verdicts,
  ...(hit.room_plan || []).flatMap((room) => room.verdicts),
];
const required = (hit) => verdicts(hit).filter((v) => v.required);
const criterionList = (parsed) =>
  ["must", "nice", "no_data"].flatMap((key) => parsed[key] || []);
const hotelId = (hit) => hit.hotel.id;
function validateResult(result) {
  assert(
    result?.parsed && Array.isArray(result.results) && result.results.length,
    "Actual search must return parsed conditions and cards",
  );
  assert.equal(
    new Set(result.results.map(hotelId)).size,
    result.results.length,
    "Duplicate hotel cards",
  );
  for (const hit of result.results) {
    assert(Array.isArray(hit.verdicts), "Missing verdicts");
    if (required(hit).some((v) => ["unknown", "no"].includes(v.status)))
      assert.notEqual(
        hit.group,
        "all",
        "Unknown/rejected required condition cannot claim all",
      );
  }
  return result;
}

// Keep the context guard installed while each page's real API relays finish.
// Rejections are reported before aborting the route, never left unhandled.
export function createR3RouteLifecycle(recordFailure) {
  const active = new Map();
  const closing = new WeakSet();
  return {
    isClosing: (page) => closing.has(page),
    run(page, route, action) {
      if (!active.has(page)) active.set(page, new Set());
      const tasks = active.get(page);
      const task = Promise.resolve()
        .then(action)
        .catch(async (error) => {
          recordFailure(error, route);
          try {
            await route.abort("failed");
          } catch (abortError) {
            recordFailure(abortError, route);
          }
        });
      tasks.add(task);
      return task.finally(() => tasks.delete(task));
    },
    async close(page) {
      closing.add(page);
      const tasks = active.get(page);
      while (tasks?.size) await Promise.all([...tasks]);
      await page.close();
      active.delete(page);
    },
  };
}

export async function runPanelR3SearchActions({
  base = ORIGIN,
  artifacts,
  browser: suppliedBrowser,
  env = process.env,
} = {}) {
  const origin = assertR3SearchRequest("GET", base, env);
  assert(
    origin.pathname === "/" && !origin.search,
    "Use a plain test-copy origin",
  );
  // Validate permission before browser creation, artifacts or any network call.
  assertR3SearchRequest("POST", new URL("/api/search", origin), env);
  const client = createR3SearchClient({ base, env });
  const output = resolve(
    artifacts ||
      fileURLToPath(
        new URL("../../runtime/panel-r3-search-actions", import.meta.url),
      ),
  );
  await mkdir(output, { recursive: true });
  const report = {
    status: "running",
    real_api: true,
    response_fixtures: false,
    stand_origin: origin.origin,
    started_at: new Date().toISOString(),
    checks: [],
    limitations: [],
    failures: [],
    captures: [],
    search_run_ids: [],
    browser_requests: [],
    forbidden_mutations: [],
    blocked_external_reads: [],
    teardown_cancellations: [],
    page_errors: [],
  };
  const pass = (id, detail = {}) =>
    report.checks.push({ id, status: "passed", ...detail });
  const limited = (id, reason) => report.limitations.push({ id, reason });
  const attempt = async (id, action) => {
    try {
      await action();
    } catch (error) {
      report.failures.push({ id, error: safeError(error) });
    }
  };
  const remember = (result) => {
    validateResult(result);
    if (result.run_id && !report.search_run_ids.includes(result.run_id))
      report.search_run_ids.push(result.run_id);
    return result;
  };
  const proofCache = new Map();
  async function proof(id, fid) {
    assert(
      /^[a-zA-Z0-9][a-zA-Z0-9._-]*$/.test(id) &&
        /^[a-zA-Z0-9][a-zA-Z0-9._-]*$/.test(fid),
      "Invalid fact identity",
    );
    const key = `${id}/${fid}`;
    if (!proofCache.has(key))
      proofCache.set(
        key,
        client.read(
          `/api/hotels/${encodeURIComponent(id)}/facts/${encodeURIComponent(fid)}/evidence`,
        ),
      );
    const value = await proofCache.get(key);
    assert(
      value.fact?.id === fid && value.fact.hotel_id === id,
      "Fact evidence belongs to another hotel",
    );
    return value;
  }
  async function support(hit, v) {
    assert(
      v.fact_ids?.length && v.evidence_refs?.length,
      "A positive verdict requires real fact/evidence references",
    );
    const values = await Promise.all(
      v.fact_ids.map((fid) => proof(hotelId(hit), fid)),
    );
    for (const { fact, claims } of values) {
      assert.equal(
        fact.characteristic_id,
        v.characteristic_id,
        "A context characteristic became primary proof",
      );
      assert(
        fact.supporting_claim_ids.length &&
          fact.supporting_claim_ids.every((id) =>
            claims.some(
              (c) =>
                c.id === id &&
                c.hotel_id === fact.hotel_id &&
                c.characteristic_id === fact.characteristic_id,
            ),
          ),
        "Broken supporting-claim chain",
      );
      if (["yes", "partial"].includes(v.status))
        assert(
          ["computed", "confirmed"].includes(fact.status) &&
            (!fact.valid_until || Date.parse(fact.valid_until) > Date.now()),
          "Positive verdict relies on stale/disputed evidence",
        );
    }
    const evidence = values.flatMap(({ fact }) => fact.evidence);
    assert(
      v.evidence_refs.every((id) => evidence.some((e) => e.id === id)),
      "Verdict evidence reference does not resolve",
    );
    return values;
  }
  let browser = suppliedBrowser,
    context;
  try {
    report.revision_before = await client.read("/api/revision");
    const results = {};
    for (const [id, text] of Object.entries(SEARCH_CASES))
      results[id] = remember(
        await client.search(text, {
          limit: ["pets", "hard_no", "wifi", "masha"].includes(id) ? 250 : 1,
        }),
      );

    await attempt("R13.center_and_dina", async () => {
      for (const id of [
        "dina_full",
        "dina_short",
        "center",
        "center_city",
        "center_relative",
        "center_district",
      ]) {
        const parsed = results[id].parsed;
        const center = criterionList(parsed).filter(
          (c) =>
            c.characteristic_id === "location_access" &&
            /central|center/.test(c.field),
        );
        const unmapped = parsed.unmapped.some((text) => /центр/i.test(text));
        const reason = parsed.clarifications.some((text) =>
          /центр/i.test(text),
        );
        if (id === "center_relative") {
          assert(
            !center.length && unmapped && reason,
            "Relative center acquired an invented condition or disappeared silently",
          );
        } else
          assert(
            center.length === 1 || (!center.length && unmapped && reason),
            `${id}: center must be one visible condition or explicitly unmapped`,
          );
        pass(`R13.${id}`, {
          center_fields: center.map((c) => c.field),
          explicitly_unmapped: unmapped,
        });
      }
    });
    await attempt("R7.real_before_demo", async () => {
      const result = results.pets;
      assert(
        result.parsed.must.length === 1 &&
          result.parsed.must[0].characteristic_id === "pets" &&
          !result.parsed.nice.length &&
          !result.parsed.no_data_must.length,
        "Pets comparison must isolate the one mandatory condition",
      );
      const real = [],
        demo = [];
      for (const [position, hit] of result.results.entries()) {
        const v = hit.verdicts.find((v) => v.characteristic_id === "pets");
        if (v?.status !== "yes") continue;
        const values = await support(hit, v);
        const evidence = values.flatMap(({ fact }) => fact.evidence);
        const genuine =
          !v.synthetic &&
          values.every(
            ({ fact, claims }) =>
              !fact.synthetic &&
              !["unknown", "disputed", "stale"].includes(fact.status) &&
              claims
                .filter((c) => fact.supporting_claim_ids.includes(c.id))
                .every(
                  (c) => !c.synthetic && c.source !== "hotelier_simulator",
                ),
          ) &&
          evidence.some((e) => e.kind === "hotelier" && e.quote?.trim()) &&
          !evidence.some(
            (e) =>
              ["simulation", "mock"].includes(e.kind) ||
              e.source_ids.includes("hotelier_simulator"),
          );
        if (v.hotelier_confirmed)
          assert(
            genuine,
            "Real-confirmed badge has no genuine quoted hotel evidence",
          );
        if (genuine) {
          assert(
            v.hotelier_confirmed,
            "Genuine pets=yes reply lost its hotel confirmation",
          );
          real.push({ position, hotel_id: hotelId(hit), fact_ids: v.fact_ids });
        }
        if (v.synthetic) {
          assert(
            !v.hotelier_confirmed &&
              evidence.some(
                (e) =>
                  e.source_ids.includes("hotelier_simulator") &&
                  e.quote?.trim(),
              ),
            "Synthetic pets=yes is not an identified demo hotel reply",
          );
          demo.push({ position, hotel_id: hotelId(hit), fact_ids: v.fact_ids });
        }
      }
      if (!real.length || !demo.length)
        return limited(
          "R7.real_before_demo",
          "No genuine pets=yes / quoted demo pair in the current seed; no ordering PASS is inferred",
        );
      assert(
        Math.max(...real.map((v) => v.position)) <
          Math.min(...demo.map((v) => v.position)),
        "A demo pets=yes precedes a genuine hotel confirmation under the isolated condition",
      );
      pass("R7.real_before_demo", {
        real,
        demo,
        meaning:
          "Observed ordering for the current evidence, not independent hotel accuracy",
      });
    });
    await attempt("R7.hard_no_below_unknown", async () => {
      const result = results.hard_no;
      const unresolved = [],
        rejected = [];
      result.results.forEach((hit, position) => {
        const items = required(hit);
        if (items.some((v) => v.status === "no"))
          rejected.push({ hotel_id: hotelId(hit), position });
        else if (items.some((v) => v.status === "unknown"))
          unresolved.push({ hotel_id: hotelId(hit), position });
      });
      if (!unresolved.length || !rejected.length)
        return limited(
          "R7.hard_no_below_unknown",
          "No observed mandatory-no / unresolved-without-no pair in this current result",
        );
      assert(
        Math.max(...unresolved.map((v) => v.position)) <
          Math.min(...rejected.map((v) => v.position)),
        "A mandatory known-no precedes an unresolved alternative without known-no",
      );
      pass("R7.hard_no_below_unknown", {
        unresolved,
        rejected,
        returned: result.results.length,
        total: result.total,
      });
      if (result.results.length < result.total)
        limited(
          "R7.full_population",
          "Only the returned search prefix was observed",
        );
    });
    await attempt("R9.unmeasured_wifi", async () => {
      const observed = [];
      for (const hit of results.wifi.results) {
        const v = hit.verdicts.find(
          (v) => v.characteristic_id === "connectivity",
        );
        if (!v || !["yes", "partial"].includes(v.status)) continue;
        const values = await support(hit, v);
        const facts = values.map(({ fact }) => fact);
        const areaOnly = values.every(
          ({ fact, claims }) =>
            ["fixed", "mobile"].includes(fact.value?.kind) ||
            [
              ...fact.evidence.flatMap((e) => e.source_ids),
              ...claims.map((c) => c.source),
            ].some((source) => /ookla/i.test(source)),
        );
        assert(
          !areaOnly,
          "Regional fixed/mobile tests were presented as hotel Wi-Fi support",
        );
        if (
          !facts.some((f) => f.value?.wifi_reported === true) ||
          facts.some((f) =>
            ["download_mbps", "upload_mbps", "latency_ms"].some(
              (key) => typeof f.value?.[key] === "number",
            ),
          )
        )
          continue;
        assert(
          v.status === "partial" &&
            /скорость (?:неизвестна|не измерена|не проверена)/i.test(v.text),
          "Unmeasured Wi-Fi must remain partial with unknown speed",
        );
        assert(
          !/\d+(?:[.,]\d+)?\s*(?:Мбит|Mbps)/i.test(v.text),
          "The desired speed became an asserted measured result",
        );
        if (/по карте/i.test(v.text))
          assert(
            facts.some((f) =>
              f.evidence.some(
                (e) =>
                  ["open_data", "calculation"].includes(e.kind) &&
                  e.source_ids.some((source) => /osm/i.test(source)),
              ),
            ),
            "Map wording has no mapped Wi-Fi source in the primary evidence",
          );
        observed.push({
          hotel_id: hotelId(hit),
          text: v.text,
          basis: v.basis,
          fact_ids: v.fact_ids,
        });
      }
      if (!observed.length)
        return limited(
          "R9.unmeasured_wifi",
          "No unmeasured reported Wi-Fi witness in the current result; measured replies do not test this boundary",
        );
      pass("R9.unmeasured_wifi", { observed });
    });
    await attempt("T3.bar94_context", async () => {
      const hit = results.masha.results.find(
        (h) => hotelId(h) === "osm-way-201474330",
      );
      if (!hit)
        return limited(
          "T3.bar94_context",
          "Brevis is absent from the current search population",
        );
      const v = hit.verdicts.find((v) => v.characteristic_id === "quiet_night");
      assert(v, "Quiet request lost its verdict");
      const detail = await client.read(
        `/api/hotels/${hotelId(hit)}?compact=true`,
      );
      const bar = detail.facts.find(
        (f) =>
          f.characteristic_id === "nightlife" &&
          typeof f.value?.nearest_bar_m === "number" &&
          Math.round(f.value.nearest_bar_m) === 94 &&
          !f.synthetic &&
          ["computed", "confirmed"].includes(f.status),
      );
      if (!bar)
        return limited(
          "T3.bar94_context",
          "The expected fresh non-synthetic Brevis bar ≈94 m is unavailable; its presence is not fabricated",
        );
      assert(
        v.context_fact_ids.includes(bar.id) && !v.fact_ids.includes(bar.id),
        "Bar context is missing or became the primary quiet proof",
      );
      const { fact } = await proof(hotelId(hit), bar.id);
      assert(
        fact.evidence.length &&
          fact.supporting_claim_ids.length &&
          v.context_evidence_refs.some((id) =>
            fact.evidence.some((e) => e.id === id),
          ),
        "Bar context lacks resolving evidence",
      );
      if (["yes", "partial"].includes(v.status)) await support(hit, v);
      else
        for (const fid of v.fact_ids)
          assert.equal(
            (await proof(hotelId(hit), fid)).fact.characteristic_id,
            "quiet_night",
            "Nightlife became the primary quiet fact",
          );
      assert(
        /бар/i.test(v.caveat || "") && /94\s*м/.test(v.caveat || ""),
        "The quiet caveat does not disclose the nearby bar",
      );
      pass("T3.bar94_context", {
        hotel_id: hotelId(hit),
        distance_m: bar.value.nearest_bar_m,
        context_fact_ids: v.context_fact_ids,
        primary_fact_ids: v.fact_ids,
      });
    });
    await attempt("R8.mixed_demo_why", async () => {
      const hit = results.masha.results[0];
      const mixed = [];
      for (const v of hit.verdicts.filter(
        (v) => v.synthetic && ["yes", "partial"].includes(v.status),
      )) {
        const values = await support(hit, v);
        if (
          values.some(
            ({ fact }) =>
              fact.evidence.some(
                (e) =>
                  e.source_ids.includes("hotelier_simulator") &&
                  e.quote?.trim(),
              ) &&
              fact.evidence.some((e) =>
                ["calculation", "open_data", "quote"].includes(e.kind),
              ),
          )
        )
          mixed.push(v);
      }
      if (!mixed.length)
        return limited(
          "R8.mixed_demo_why",
          "No mixed demo/calculation leader in current Masha results; no mixed-source PASS is inferred",
        );
      assert(
        /демо/i.test(hit.why_higher || "") &&
          /вместе|расч[её]т|источник/i.test(hit.why_higher),
        "Mixed leader lacks its explicit source-composition explanation",
      );
      assert(
        !/из-за|благодаря|только.*демо/i.test(hit.why_higher),
        "Mixed evidence acquired an unsupported causal/only-demo explanation",
      );
      pass("R8.mixed_demo_why", {
        hotel_id: hotelId(hit),
        text: hit.why_higher,
        fact_ids: mixed.flatMap((v) => v.fact_ids),
      });
    });

    const executable =
      process.env.OKNO_BROWSER_EXECUTABLE ||
      "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
    if (!browser && existsSync(executable)) {
      const { chromium } = await import("playwright");
      browser = await chromium.launch({
        headless: true,
        executablePath: executable,
      });
    }
    if (!browser)
      limited(
        "DOM",
        "No supplied browser/system Chrome: because, headings and visible labels were not checked",
      );
    else {
      context = await browser.newContext({
        viewport: { width: 1440, height: 1000 },
        serviceWorkers: "block",
      });
      const routeLifecycle = createR3RouteLifecycle((error, route) => {
        const request = route.request();
        report.failures.push({
          id: "browser_route",
          method: request.method(),
          path: new URL(request.url()).pathname,
          error: safeError(error),
        });
      });
      await context.route("**/*", (route) => {
        const request = route.request(),
          url = new URL(request.url());
        let page;
        try {
          page = request.frame().page();
        } catch (error) {
          return routeLifecycle.run(null, route, () => {
            throw error;
          });
        }
        return routeLifecycle.run(page, route, async () => {
          try {
            assertR3SearchRequest(request.method(), url, env);
          } catch (error) {
            if (readOnly.has(request.method()) && url.origin !== origin.origin)
              report.blocked_external_reads.push(url.hostname);
            else
              report.forbidden_mutations.push({
                method: request.method(),
                path: url.pathname,
                error: safeError(error),
              });
            return route.abort("blockedbyclient");
          }
          if (routeLifecycle.isClosing(page)) {
            // Only not-yet-forwarded reads can be cancelled as teardown work.
            // A new search POST after checks is an unexpected mutation attempt.
            if (!readOnly.has(request.method()))
              report.forbidden_mutations.push({
                method: request.method(),
                path: url.pathname,
                error: "New mutation after page checks finished",
              });
            report.teardown_cancellations.push({
              method: request.method(),
              path: url.pathname,
              reason: "page_checks_finished_before_forwarding",
              expected: readOnly.has(request.method()),
            });
            return route.abort("aborted");
          }
          if (url.pathname.startsWith("/api/")) {
            // Relay the actual API response unchanged; this is not a fixture.
            // A browser redirect must not turn an allowed search into a second,
            // unguarded POST (including a same-origin answer/passport endpoint).
            const response = await route.fetch({
              maxRedirects: 0,
              timeout: 60000,
            });
            report.browser_requests.push({
              method: request.method(),
              path: url.pathname,
              status: response.status(),
            });
            if (response.status() >= 300 && response.status() < 400) {
              report.forbidden_mutations.push({
                method: request.method(),
                path: url.pathname,
                error: "API redirect was blocked before following it",
              });
              return route.abort("blockedbyclient");
            }
            assert(
              response.ok(),
              `${request.method()} ${url.pathname}: HTTP ${response.status()}`,
            );
            return route.fulfill({ response });
          }
          return route.continue();
        });
      });
      for (const id of ["masha", "wifi", "dina_full", "center_relative"])
        await attempt(`DOM.${id}`, async () => {
          const page = await context.newPage();
          page.on("pageerror", (error) =>
            report.page_errors.push(safeError(error)),
          );
          try {
            const pending = page.waitForResponse(
              (r) =>
                new URL(r.url()).pathname === "/api/search" &&
                r.request().method() === "POST",
              { timeout: 65000 },
            );
            await page.goto(
              `${origin.origin}/search?q=${encodeURIComponent(SEARCH_CASES[id])}`,
              { waitUntil: "domcontentloaded" },
            );
            const response = await pending;
            assert(
              response.ok(),
              `Actual browser search HTTP ${response.status()}`,
            );
            const result = remember(await response.json());
            await page
              .getByTestId(`hotel-card-${hotelId(result.results[0])}`)
              .waitFor();
            if (id === "masha") {
              let reasons = 0,
                nonYes = 0;
              for (const hit of result.results) {
                const card = page.getByTestId(`hotel-card-${hotelId(hit)}`);
                if (!(await card.count())) continue;
                const roomIds = new Set([
                  "quiet_night",
                  "sea_view",
                  "sunset_view",
                  "soundproofing",
                  "mountain_view",
                  "room_orientation",
                  "morning_sun",
                  "evening_heat",
                  "railway_noise",
                  "landmark_view",
                ]);
                const wanted = criterionList(result.parsed).filter((c) =>
                  roomIds.has(c.characteristic_id),
                );
                nonYes += wanted.filter((c) =>
                  hit.verdicts.some(
                    (v) =>
                      v.characteristic_id === c.characteristic_id &&
                      v.status !== "yes",
                  ),
                ).length;
                const allowed = new Set(
                  wanted.flatMap((c) => {
                    const v = hit.verdicts.find(
                      (v) =>
                        v.characteristic_id === c.characteristic_id &&
                        v.status === "yes" &&
                        !v.disputed,
                    );
                    return v
                      ? [
                          normalize(c.label.split(/[≤≥=]/)[0]) +
                            (v.synthetic ? " (ответ отеля · демо)" : ""),
                        ]
                      : [];
                  }),
                );
                for (const text of await card
                  .locator(".room-request-reason")
                  .allTextContents()) {
                  const phrases = normalize(text)
                    .replace(/^потому что:\s*/, "")
                    .replace(/\.$/, "")
                    .split(/,\s*/);
                  assert(
                    phrases.every((phrase) => allowed.has(phrase)),
                    `Because mentions a non-yes/disputed condition or omits demo: ${hotelId(hit)}`,
                  );
                  reasons++;
                }
              }
              if (reasons && nonYes)
                pass("R8.because_only_yes.DOM", {
                  reasons,
                  non_yes_room_conditions: nonYes,
                });
              else
                limited(
                  "R8.because_only_yes.DOM",
                  "Current rendered cards do not exercise both a reason and a non-yes room condition",
                );
              if (
                result.results.some(
                  (h) =>
                    h.group === "all" &&
                    required(h).some(
                      (v) =>
                        v.status === "yes" &&
                        v.synthetic &&
                        !["calculation", "open_data"].includes(
                          v.basis || "unknown",
                        ),
                    ),
                )
              ) {
                assert.equal(
                  await page.getByText(/^ВАШИ УСЛОВИЯ СОШЛИСЬ$/i).count(),
                  0,
                  "An all/demo group has an unconditional success heading",
                );
                assert(
                  await page
                    .getByText(
                      /В совпадениях есть демо-ответы — подтвердите их у отеля/i,
                    )
                    .isVisible(),
                  "All/demo group lacks the neutral confirmation caveat",
                );
                pass("R8.demo_group_heading.DOM");
              } else
                limited(
                  "R8.demo_group_heading.DOM",
                  "No all-group required demo=yes witness is currently rendered",
                );
              const leader = result.results[0];
              if (leader.why_higher) {
                assert.equal(
                  normalize(
                    await page
                      .getByTestId(`hotel-card-${hotelId(leader)}`)
                      .getByTestId("why-higher")
                      .innerText(),
                  ),
                  normalize(leader.why_higher),
                );
                pass("R8.why_higher_visible.DOM", {
                  hotel_id: hotelId(leader),
                });
              } else
                limited(
                  "R8.why_higher_visible.DOM",
                  "The current leader has no why_higher field",
                );
            } else if (id === "wifi") {
              let count = 0;
              for (const hit of result.results) {
                const v = hit.verdicts.find(
                  (v) =>
                    v.characteristic_id === "connectivity" &&
                    v.status === "partial" &&
                    /скорость (?:неизвестна|не измерена|не проверена)/i.test(
                      v.text,
                    ),
                );
                if (!v) continue;
                const card = page.getByTestId(`hotel-card-${hotelId(hit)}`);
                if (!(await card.count())) continue;
                const labels = await card
                  .locator(".verdict-text strong")
                  .allTextContents();
                assert(
                  labels.some(
                    (label) => normalize(label) === normalize(v.text),
                  ),
                  "Unmeasured Wi-Fi still shows the desired speed as its result headline",
                );
                count++;
              }
              if (count)
                pass("R9.unknown_speed_headline.DOM", { cards: count });
              else
                limited(
                  "R9.unknown_speed_headline.DOM",
                  "No unmeasured Wi-Fi partial is in the rendered prefix",
                );
            } else if (id === "dina_full") {
              const center = criterionList(result.parsed).find(
                (c) =>
                  c.characteristic_id === "location_access" &&
                  c.field === "in_central_sochi",
              );
              if (center)
                assert(
                  (
                    await page
                      .locator(".criteria-panel .criterion-chip")
                      .allTextContents()
                  ).some((text) =>
                    normalize(text).includes(normalize(center.label)),
                  ),
                  "Dina's center criterion is not visible",
                );
              else
                assert(
                  /центр/i.test(
                    await page.locator(".criteria-panel").innerText(),
                  ) &&
                    /не удалось превратить/i.test(
                      await page.locator(".criteria-panel").innerText(),
                    ),
                  "Unmapped center is not disclosed",
                );
              pass("R13.dina_full_visible.DOM");
            } else {
              const text = await page.locator(".criteria-panel").innerText();
              assert(
                /ближе к центру/i.test(text) &&
                  /не удалось превратить/i.test(text),
                "Relative center disappeared from the visible unmapped explanation",
              );
              pass("R13.relative_center_visible.DOM");
            }
            const name = `${id}.png`;
            await page.screenshot({
              path: resolve(output, name),
              fullPage: false,
            });
            report.captures.push(name);
          } finally {
            await routeLifecycle.close(page);
          }
        });
    }
    report.revision_after = await client.read("/api/revision");
    if (
      JSON.stringify(report.revision_before) !==
      JSON.stringify(report.revision_after)
    )
      limited(
        "revision",
        "Catalog revision changed during the audit; repeat serially before treating observations as one state",
      );
    assert(
      !report.forbidden_mutations.length,
      "A browser attempted a forbidden mutation; it was blocked",
    );
    assert(!report.page_errors.length, "Browser page errors occurred");
  } catch (error) {
    report.failures.push({ id: "runner", error: safeError(error) });
  } finally {
    try {
      if (context) await context.close();
    } catch (error) {
      report.failures.push({ id: "context_cleanup", error: safeError(error) });
    }
    try {
      if (browser && !suppliedBrowser) {
        const { closeSmokeBrowser } = await import(
          "./panel-guest-smoke-ui.mjs"
        );
        await closeSmokeBrowser(browser);
      }
    } catch (error) {
      report.failures.push({ id: "browser_cleanup", error: safeError(error) });
    }
    report.requests = client.requests;
    report.status =
      report.failures.length ||
      report.forbidden_mutations.length ||
      report.page_errors.length
        ? "failed"
        : report.limitations.length
          ? "completed_with_limitations"
          : "passed";
    report.finished_at = new Date().toISOString();
    await writeFile(
      resolve(output, "panel-r3-search-actions.json"),
      JSON.stringify(report, null, 2) + "\n",
    );
  }
  if (report.status === "failed")
    throw Object.assign(
      new Error("R3 search audit failed; inspect panel-r3-search-actions.json"),
      { report },
    );
  return report;
}
