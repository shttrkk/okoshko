import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { closeSmokeBrowser } from "./panel-guest-smoke-ui.mjs";

// T11 only: explicit network fault injection, never evidence about real hotels.
// Every /api request is intercepted. No POST or API GET reaches the server.
// root may import this helper alongside the separate actual-API action helpers.
const query = "Тестовая подмена сети T11: настоящие отели не проверяются";
const readOnly = new Set(["GET", "HEAD", "OPTIONS"]);
const provider = (host) =>
  /(^|\.)(?:yandex\.[a-z.]+|ya\.ru|mapillary\.[a-z.]+)$/i.test(host);
const statusFixture = {
  name: "T11 network fault injection",
  mode: "offline",
  version: "test-only",
  hotel_count: 0,
  characteristic_count: 0,
  seed_version: "no-real-data",
};
const searchFixture = {
  run_id: "t11-network-fault-only",
  total: 0,
  results: [],
  mode: "offline",
  elapsed_ms: 0,
  parsed: {
    text: query,
    must: [],
    nice: [],
    rooms: [],
    no_data: [],
    no_data_must: [],
    unmapped: [],
    clarifications: [],
    month: 9,
    check_in: null,
    check_out: null,
    mode: "offline",
  },
};

export async function runPanelR3Loading({ base, artifacts } = {}) {
  const origin = new URL(String(base || "http://localhost:8081"));
  assert(
    ["http:", "https:"].includes(origin.protocol) &&
      ["localhost", "127.0.0.1"].includes(origin.hostname),
    "T11 fault test requires a local application origin",
  );
  assert(
    !origin.username &&
      !origin.password &&
      !origin.search &&
      !origin.hash &&
      ["", "/"].includes(origin.pathname),
    "Use a plain local application origin",
  );
  const output = resolve(
    artifacts ||
      fileURLToPath(new URL("../../runtime/panel-r3-loading", import.meta.url)),
  );
  const chrome =
    process.env.OKNO_BROWSER_EXECUTABLE ||
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
  assert(
    existsSync(chrome),
    "System Chrome is required; this helper never downloads a browser",
  );
  await mkdir(output, { recursive: true });
  const report = {
    test_copy: true,
    fault_injection: true,
    real_api: false,
    real_hotel_facts: false,
    status: "running",
    started_at: new Date().toISOString(),
    application_origin: origin.origin,
    test_query: query,
    checks: [],
    captures: [],
    cases: [],
    blocked_providers: [],
    blocked_mutations: [],
    mocked_api_requests: [],
    page_errors: [],
    errors: [],
  };
  const reportFile = resolve(output, "panel-r3-loading.json");
  const save = () =>
    writeFile(reportFile, JSON.stringify(report, null, 2) + "\n");
  const check = (condition, label) => {
    assert.ok(condition, label);
    report.checks.push(label);
  };
  const browser = await chromium.launch({
    headless: true,
    executablePath: chrome,
  });
  const contexts = new Set(),
    holds = new Set();
  let stage = "initialization";
  function hold(route) {
    let release;
    const pending = new Promise((done) => {
      release = done;
    });
    const entry = {
      release,
      done: pending.then(async (value) => {
        try {
          if (value) await route.fulfill({ json: value });
          else await route.abort("blockedbyclient");
        } catch {
          /* The browser may already have aborted a timed-out request. */
        }
        holds.delete(entry);
      }),
    };
    holds.add(entry);
    return entry;
  }
  async function surface(kind, width) {
    const context = await browser.newContext({
      viewport: { width, height: width === 390 ? 844 : 1000 },
      colorScheme: "light",
      serviceWorkers: "block",
    });
    contexts.add(context);
    const state = {
      kind,
      searchMode: kind === "search" ? "hold" : "success",
      statusMode: kind === "layout" ? "hold" : "success",
      searchRequests: 0,
      statusRequests: 0,
      abortedSearches: 0,
      heldStatus: null,
    };
    // Context-wide interception is installed before the first page/navigation.
    await context.route("**/*", async (route) => {
      const request = route.request(),
        url = new URL(request.url()),
        method = request.method();
      if (provider(url.hostname)) {
        report.blocked_providers.push({
          host: url.hostname,
          resource: request.resourceType(),
        });
        return route.abort("blockedbyclient");
      }
      if (url.origin !== origin.origin) return route.abort("blockedbyclient");
      if (url.pathname.startsWith("/api/")) {
        report.mocked_api_requests.push({
          case: kind,
          width,
          method,
          path: url.pathname,
        });
        if (url.pathname === "/api/search" && method === "POST") {
          state.searchRequests += 1;
          if (state.searchMode === "hold") return hold(route).done;
          if (state.searchMode === "fail") return route.abort("failed");
          return route.fulfill({ json: searchFixture });
        }
        if (!readOnly.has(method)) {
          report.blocked_mutations.push({ method, path: url.pathname });
          return route.abort("blockedbyclient");
        }
        if (url.pathname === "/api/status") {
          state.statusRequests += 1;
          if (state.statusMode === "hold") {
            const entry = hold(route);
            state.heldStatus = entry;
            return entry.done;
          }
          return route.fulfill({ json: statusFixture });
        }
        if (url.pathname === "/api/revision")
          return route.fulfill({
            json: { instance: "t11-fault-injection-only", revision: 1 },
          });
        if (
          ["/api/hotels", "/api/characteristics", "/api/sources"].includes(
            url.pathname,
          )
        )
          return route.fulfill({ json: [] });
        return route.fulfill({
          status: 404,
          json: {
            detail: "Тестовая подмена сети T11: этот API не используется",
          },
        });
      }
      if (!readOnly.has(method)) {
        report.blocked_mutations.push({ method, path: url.pathname });
        return route.abort("blockedbyclient");
      }
      return route.continue(); // Only static application assets; never an API/write.
    });
    const page = await context.newPage();
    page.setDefaultTimeout(15000);
    page.on("pageerror", (error) =>
      report.page_errors.push(String(error.message)),
    );
    page.on("requestfailed", (request) => {
      if (new URL(request.url()).pathname === "/api/search")
        state.abortedSearches += 1;
    });
    await page.clock.install({ time: new Date("2026-10-28T12:00:00Z") });
    return { page, state, context };
  }
  async function capture(page, label, locator) {
    await locator.scrollIntoViewIfNeeded();
    const file = `panel-r3-loading-${label}-${page.viewportSize().width}.png`;
    await page.screenshot({ path: resolve(output, file), fullPage: false });
    report.captures.push({
      label,
      width: page.viewportSize().width,
      file,
      fault_injection: true,
    });
  }
  async function cleanRussian(page, label) {
    const text = await page.locator("body").innerText();
    check(
      !/Failed to fetch|NetworkError|ERR_FAILED|Ожидаем подключение API/i.test(
        text,
      ),
      label + ": Russian loading/error copy without raw network messages",
    );
  }
  try {
    for (const width of [1440, 390]) {
      stage = `Search ${width}`;
      const search = await surface("search", width),
        { page, state } = search;
      await page.goto(
        new URL(`/search?q=${encodeURIComponent(query)}`, origin).href,
        { waitUntil: "domcontentloaded" },
      );
      await page.locator(".search-list .loading").waitFor();
      await page.clock.runFor(50);
      check(
        state.searchRequests === 1,
        `${stage}: one delayed synthetic search is intercepted`,
      );
      await page.clock.fastForward(8100);
      await page.waitForFunction(() =>
        document
          .querySelector(".search-list .loading")
          ?.textContent.includes("дольше обычного"),
      );
      await cleanRussian(page, stage + " slow");
      await capture(
        page,
        "search-slow-8s",
        page.locator(".search-list .loading"),
      );
      await page.clock.fastForward(22000);
      const error = page.locator(".search-list .error-state");
      await error.waitFor();
      check(
        /Поиск не завершён/.test(await error.innerText()) &&
          /30 секунд/.test(await error.innerText()),
        `${stage}: at 30 seconds search stops with a useful error`,
      );
      check(
        (await page.locator(".search-list .loading").count()) === 0,
        `${stage}: there is no endless spinner after timeout`,
      );
      check(state.abortedSearches >= 1, `${stage}: timed-out fetch is aborted`);
      await cleanRussian(page, stage + " timeout");
      await capture(page, "search-error-30s", error);
      state.searchMode = "success";
      await error
        .getByRole("button", { name: "Повторить", exact: true })
        .click();
      await page.locator(".results-footnote").waitFor();
      check(
        state.searchRequests === 2 && (await error.count()) === 0,
        `${stage}: retry performs another intercepted request and clears the error`,
      );
      await page.clock.fastForward(31000);
      check(
        (await page.locator(".search-list .error-state").count()) === 0 &&
          (await page.locator(".search-list .loading").count()) === 0,
        `${stage}: old timeout/loading timers do not reappear after retry`,
      );
      await capture(page, "search-retry", page.locator(".results-footnote"));
      state.searchMode = "fail";
      await page.goto(
        new URL(
          `/search?q=${encodeURIComponent(query + " — мгновенный сетевой сбой")}`,
          origin,
        ).href,
      );
      await error.waitFor();
      check(
        /Не удалось связаться с сервисом/.test(await error.innerText()),
        `${stage}: immediate network failure has human Russian text`,
      );
      await cleanRussian(page, stage + " network fault");
      report.cases.push({
        target: "search",
        width,
        fault_injection: true,
        slow_after_ms: 8100,
        error_after_ms: 30100,
        retry: "passed",
        aborted_fetch: true,
        immediate_network_failure: "Russian",
      });
      await save();
      for (const entry of holds) entry.release(null);
      await search.context.close();
      contexts.delete(search.context);

      stage = `Layout ${width}`;
      const layout = await surface("layout", width);
      await layout.page.goto(origin.href, { waitUntil: "domcontentloaded" });
      const connection = layout.page.getByTestId("connection-status");
      await connection.waitFor();
      await layout.page.clock.runFor(50);
      check(
        layout.state.statusRequests === 1,
        `${stage}: delayed status request is intercepted`,
      );
      check(
        (await connection.innerText()).includes("Загружаем сведения"),
        `${stage}: initial connection message is human`,
      );
      await layout.page.clock.fastForward(8100);
      await layout.page.waitForFunction(() =>
        document
          .querySelector('[data-testid="connection-status"]')
          ?.textContent.includes("дольше обычного"),
      );
      await cleanRussian(layout.page, stage + " slow");
      await capture(layout.page, "layout-slow-8s", connection);
      await layout.page.clock.fastForward(22000);
      const retry = layout.page.getByRole("button", {
        name: "Повторить загрузку сведений",
        exact: true,
      });
      await retry.waitFor();
      check(
        (await connection.innerText()).includes(
          "Не удалось загрузить сведения",
        ),
        `${stage}: after 30 seconds the footer explains the failure`,
      );
      await cleanRussian(layout.page, stage + " timeout");
      await capture(layout.page, "layout-error-30s", connection);
      layout.state.statusMode = "success";
      await retry.click();
      await connection.waitFor({ state: "detached" });
      check(
        layout.state.statusRequests === 2 &&
          (await layout.page.locator(".site-footer").innerText()).includes(
            "0 отелей",
          ),
        `${stage}: retry replaces the failed footer with the synthetic status response`,
      );
      const firstResponse = layout.state.heldStatus;
      firstResponse.release({ ...statusFixture, hotel_count: 999 });
      await firstResponse.done;
      await layout.page.clock.runFor(50);
      check(
        !(await layout.page.locator(".site-footer").innerText()).includes(
          "999 отелей",
        ),
        `${stage}: a stale delayed status response cannot overwrite retry data`,
      );
      await layout.page.clock.fastForward(31000);
      check(
        (await connection.count()) === 0,
        `${stage}: obsolete loading/error timers are cleaned after retry`,
      );
      await capture(
        layout.page,
        "layout-retry",
        layout.page.locator(".site-footer"),
      );
      report.cases.push({
        target: "layout",
        width,
        fault_injection: true,
        slow_after_ms: 8100,
        error_after_ms: 30100,
        retry: "passed",
        stale_response_ignored: true,
      });
      await layout.context.close();
      contexts.delete(layout.context);
      await save();
    }
    check(
      report.blocked_mutations.length === 0,
      "Fault test attempted no non-search mutations",
    );
    check(
      report.page_errors.length === 0,
      "Fault injection caused no uncaught JavaScript errors",
    );
    report.status = "passed";
    return report;
  } catch (error) {
    report.status = "failed";
    report.errors.push({ stage, error: String(error.message || error) });
    throw new Error(
      `T11 fault injection failed at ${stage}: ${error.message || error}`,
    );
  } finally {
    for (const entry of holds) entry.release(null);
    for (const context of contexts) await context.close().catch(() => {});
    await closeSmokeBrowser(browser);
    report.finished_at = new Date().toISOString();
    await save();
  }
}

if (
  process.argv[1] &&
  resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  runPanelR3Loading({
    base: process.env.OKNO_BASE_URL || "http://localhost:8081",
    artifacts: process.env.OKNO_PANEL_R3_ARTIFACTS,
  })
    .then((report) => {
      console.log(
        JSON.stringify({
          status: report.status,
          fault_injection: true,
          real_api: false,
          checks: report.checks.length,
          captures: report.captures.length,
        }),
      );
      process.exit(0);
    })
    .catch((error) => {
      console.error(String(error.message || error));
      process.exit(1);
    });
}
