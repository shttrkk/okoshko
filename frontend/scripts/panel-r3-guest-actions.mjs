import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { closeSmokeBrowser } from "./panel-guest-smoke-ui.mjs";

// Actual API/UI acceptance on a disposable local test copy. No response fixtures.
// Run only after root confirms the rebuilt application is ready:
// OKNO_BASE_URL=http://localhost:8081 OKNO_PANEL_R3_WRITES=1 node scripts/panel-r3-guest-actions.mjs
export function assertR3GuestWriteTarget(value) {
  const url = new URL(String(value));
  assert.equal(
    process.env.OKNO_PANEL_R3_WRITES,
    "1",
    "Set OKNO_PANEL_R3_WRITES=1 for the disposable test copy",
  );
  assert(["http:", "https:"].includes(url.protocol), "HTTP(S) is required");
  assert(
    ["localhost", "127.0.0.1"].includes(url.hostname) && url.port === "8081",
    "Writes are restricted to localhost/127.0.0.1:8081",
  );
  assert(
    !url.username &&
      !url.password &&
      !url.search &&
      !url.hash &&
      ["", "/"].includes(url.pathname),
    "Use a plain local test-copy origin",
  );
  return url;
}
const READ_ONLY = new Set(["GET", "HEAD", "OPTIONS"]);
const query = Object.freeze({
  masha: "тихий номер с видом на закат недалеко от моря",
  input:
    "Тестовая копия R3: едем с подругами и собакой, нужен рабочий интернет",
  stroller: "Едем с коляской, до пляжа без лестниц",
  group:
    "Едем компанией: родителям — тихий номер; нам — вид на море. Для всех — до пляжа не больше 10 минут",
  studio: "Аптека ближе 300 м",
});
const trip = { check_in: "2027-09-14", check_out: "2027-09-21" };
const guestName = "Тестовая копия R3";
const hotelComment =
  "Тестовая копия R3: номер 305, юго-запад, третий этаж. Пожелания согласованы в кабинете отеля.";
const provider = (host) =>
  /(^|\.)(?:yandex\.[a-z.]+|ya\.ru|mapillary\.[a-z.]+)$/i.test(host);
const safeError = (error) =>
  String(error?.message || error)
    .replace(/\bBearer\s+\S+/gi, "Bearer [redacted]")
    .replace(/\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_.-]+/g, "[token redacted]")
    .replace(/https?:\/\/[^\s"'<>]+/g, (value) => {
      try {
        const url = new URL(value);
        return url.origin + url.pathname;
      } catch {
        return "[URL]";
      }
    });
const delay = (milliseconds) =>
  new Promise((done) => setTimeout(done, milliseconds));

export async function runPanelR3GuestActions({ base, artifacts } = {}) {
  // This guard precedes browser creation, network traffic and every possible POST.
  const origin = assertR3GuestWriteTarget(base || "http://localhost:8081");
  const output = resolve(
    artifacts ||
      fileURLToPath(
        new URL("../../runtime/panel-r3-guest-actions", import.meta.url),
      ),
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
    real_api: true,
    response_fixtures: false,
    status: "running",
    stand_origin: origin.origin,
    started_at: new Date().toISOString(),
    test_queries: query,
    checks: [],
    captures: [],
    allowed_posts: {},
    blocked_providers: [],
    forbidden_mutations: [],
    page_errors: [],
    stages: {},
    errors: [],
  };
  const contexts = new Set(),
    testPassportIds = new Set();
  let browser,
    token = null,
    compiledId = null,
    stage = "initialization",
    failurePage = null;
  const reportPath = resolve(output, "panel-r3-guest-actions.json");
  const save = () =>
    writeFile(reportPath, JSON.stringify(report, null, 2) + "\n");
  const check = (condition, label) => {
    assert.ok(condition, label);
    report.checks.push(label);
  };
  const pathLabel = (path) =>
    path.replace(/\/passports\/[^/]+/, "/passports/<test-passport>");
  function permitted(method, url, body) {
    if (READ_ONLY.has(method)) return true;
    // Re-check the environment and target immediately before each mutation.
    assertR3GuestWriteTarget(origin);
    if (method !== "POST" || url.origin !== origin.origin) return false;
    if (
      [
        "/api/search",
        "/api/compare",
        "/api/characteristics/compile",
        "/api/characteristics/dry-run",
        "/api/hotelier/login",
        "/api/hotelier/logout",
      ].includes(url.pathname)
    )
      return true;
    if (/^\/api\/hotels\/[^/]+\/evaluate$/.test(url.pathname)) return true;
    if (url.pathname === "/api/characteristics/publish")
      return !!compiledId && body?.id === compiledId;
    if (url.pathname === "/api/passports")
      return body?.guest_name === guestName;
    const action = url.pathname.match(/^\/api\/passports\/([^/]+)\/action$/);
    return (
      !!action &&
      testPassportIds.has(action[1]) &&
      body?.action === "confirm" &&
      body.room_number === "305" &&
      body.text === hotelComment
    );
  }
  function recordPost(path) {
    const label = pathLabel(path);
    report.allowed_posts[label] = (report.allowed_posts[label] || 0) + 1;
  }
  async function api(path, body, authenticated = false) {
    const url = new URL(path, origin),
      method = body === undefined ? "GET" : "POST";
    check(
      url.origin === origin.origin && !provider(url.hostname),
      "API stays on the local test copy",
    );
    check(
      permitted(method, url, body),
      "API mutation guard permits only the requested test action",
    );
    if (method === "POST") recordPost(url.pathname);
    const response = await fetch(url, {
      method,
      redirect: "manual",
      credentials: "omit",
      signal: AbortSignal.timeout(45000),
      headers: {
        Accept: "application/json",
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
        ...(authenticated && token ? { Authorization: `Bearer ${token}` } : {}),
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    check(
      response.ok,
      `${method} ${pathLabel(url.pathname)} returned HTTP ${response.status}`,
    );
    return response.json();
  }
  async function surface(width, scheme = "light") {
    const context = await browser.newContext({
      viewport: { width, height: width === 390 ? 844 : 1000 },
      colorScheme: scheme,
      serviceWorkers: "block",
    });
    contexts.add(context);
    await context.route("**/*", async (route) => {
      const request = route.request(),
        url = new URL(request.url());
      if (provider(url.hostname)) {
        report.blocked_providers.push({
          host: url.hostname,
          resource: request.resourceType(),
        });
        return route.abort("blockedbyclient");
      }
      if (request.isNavigationRequest() && url.origin !== origin.origin)
        return route.abort("blockedbyclient");
      let body;
      if (!READ_ONLY.has(request.method())) {
        try {
          body = request.postDataJSON();
        } catch {
          body = null;
        }
      }
      if (!permitted(request.method(), url, body)) {
        report.forbidden_mutations.push({
          method: request.method(),
          path: pathLabel(url.pathname),
        });
        return route.abort("blockedbyclient");
      }
      if (request.method() === "POST") recordPost(url.pathname);
      return route.continue();
    });
    const page = await context.newPage();
    page.setDefaultTimeout(45000);
    page.on("pageerror", (error) => report.page_errors.push(safeError(error)));
    return page;
  }
  async function capture(page, name, focus) {
    if (focus) await focus.scrollIntoViewIfNeeded();
    const filename = `panel-r3-guest-${name}.png`;
    await page.screenshot({ path: resolve(output, filename), fullPage: false });
    report.captures.push({
      name,
      file: filename,
      width: page.viewportSize().width,
    });
  }
  async function bothWidths(page, name, focus) {
    if (await page.locator(".booking-page").count()) {
      await page.locator(".booking-hotel-label").waitFor();
      await page.waitForFunction(
        () => !document.querySelector(".booking-page .loading"),
      );
    }
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
      await capture(page, `${name}-${width}`, focus);
    }
    await page.setViewportSize({ width: 1440, height: 1000 });
  }
  async function uiPost(page, path, action) {
    const pending = page.waitForResponse(
      (response) =>
        new URL(response.url()).pathname === path &&
        response.request().method() === "POST",
    );
    await action();
    const response = await pending;
    check(response.ok(), `UI ${pathLabel(path)} succeeded`);
    return response.json();
  }
  async function home(page) {
    failurePage = page;
    await page.goto(origin.href, { waitUntil: "domcontentloaded" });
    await page.getByTestId("search-input").waitFor();
  }
  async function dates(page) {
    await page.getByTestId("check-in").fill(trip.check_in);
    await page.getByTestId("check-out").fill(trip.check_out);
  }
  async function searchUI(page, text, oneClick = false) {
    await home(page);
    await dates(page);
    if (!oneClick) await page.getByTestId("search-input").fill(text);
    const result = await uiPost(page, "/api/search", () =>
      page.getByTestId(oneClick ? "masha-example" : "search-submit").click(),
    );
    check(result.results.length > 0, "Actual UI search returns hotels");
    await page.locator(".result-card").first().waitFor();
    return result;
  }
  const allVerdicts = (hit) => [
    ...hit.verdicts,
    ...(hit.room_plan || []).flatMap((item) => item.verdicts),
  ];
  try {
    browser = await chromium.launch({ headless: true, executablePath: chrome });
    const page = await surface(1440);
    stage = "R10";
    for (const [width, scheme] of [
      [1440, "light"],
      [390, "dark"],
    ]) {
      const form = width === 1440 ? page : await surface(width, scheme);
      await home(form);
      await dates(form);
      const input = form.getByTestId("search-input"),
        original = query.input;
      await input.fill(original);
      const originalUrl = form.url();
      let searches = 0;
      const countSearch = (request) => {
        if (
          request.method() === "POST" &&
          new URL(request.url()).pathname === "/api/search"
        )
          searches += 1;
      };
      form.on("request", countSearch);
      await form.getByTestId("group-search-example").click();
      await form.getByTestId("company-search-help").waitFor();
      check(
        (await input.inputValue()) === original,
        `R10 ${width}: company help preserves the exact query`,
      );
      const small = await input.evaluate(
        (element) => element.getBoundingClientRect().height,
      );
      const long = `${original}\n${Array(7).fill("Тестовый дополнительный абзац для проверки роста поля.").join("\n")}`;
      await input.fill(long);
      await form.waitForFunction(() => {
        const element = document.querySelector('[data-testid="search-input"]');
        return element && element.clientHeight >= element.scrollHeight - 2;
      });
      check(
        (await input.evaluate(
          (element) => element.getBoundingClientRect().height,
        )) > small,
        `R10 ${width}: textarea grows with the test input`,
      );
      await form.getByTestId("masha-example").click();
      await form.getByTestId("restore-search-input").waitFor();
      check(
        form.url() === originalUrl && searches === 0,
        `R10 ${width}: nonempty Masha example does not search`,
      );
      await form.getByTestId("restore-search-input").click();
      check(
        (await input.inputValue()) === long,
        `R10 ${width}: restore returns the original input`,
      );
      check(
        (await form.getByTestId("check-in").inputValue()) === trip.check_in &&
          (await form.getByTestId("check-out").inputValue()) === trip.check_out,
        `R10 ${width}: dates survive the examples`,
      );
      await capture(form, `r10-input-${width}-${scheme}`, input);
      form.off("request", countSearch);
      await input.fill("");
      const result = await uiPost(form, "/api/search", () =>
        form.getByTestId("masha-example").click(),
      );
      check(
        result.parsed.check_in === trip.check_in &&
          result.parsed.check_out === trip.check_out,
        `R10 ${width}: empty-input Masha is still one-click with dates`,
      );
      await form.locator(".result-card").first().waitFor();
    }
    report.stages.R10 = {
      status: "passed",
      widths: [1440, 390],
      mobile_theme: "dark",
    };
    await save();

    stage = "R6";
    const masha = await searchUI(page, query.masha);
    const cascade = masha.results.find((hit) =>
      /cascade/i.test(hit.hotel.name),
    );
    check(!!cascade, "R6: actual Masha results contain Cascade");
    check(
      cascade.room_profile?.scope.facade === "SW" &&
        cascade.room_profile.scope.floor_min === 3,
      "R6: Cascade recommendation is southwest on floor 3",
    );
    await page.goto(
      new URL(`/booking/${encodeURIComponent(cascade.hotel.id)}`, origin).href,
    );
    await page.getByTestId("passport-guest").waitFor();
    check(
      (await page.getByLabel("Заезд в паспорте").inputValue()) ===
        trip.check_in &&
        (await page.getByLabel("Выезд в паспорте").inputValue()) ===
          trip.check_out,
      "R6: booking retains the requested trip dates",
    );
    check(
      /юго-запад.*3-й этаж/s.test(
        await page.getByTestId("passport-recommended-scope").innerText(),
      ),
      "R6: booking shows the selected southwest / third floor",
    );
    await page.getByTestId("passport-guest").fill(guestName);
    const created = await uiPost(page, "/api/passports", () =>
      page.getByTestId("passport-submit").click(),
    );
    testPassportIds.add(created.id);
    check(
      created.status !== "at_risk",
      "R6: future TTL alone does not put a new passport at risk",
    );
    const reviews = created.timeline.filter(
      (event) => event.type === "evidence_review" && !event.resolved,
    );
    check(
      reviews.length === 1 && reviews[0].review_by,
      "R6: one shared future review deadline is retained",
    );
    await page.getByTestId("passport-saved-scope").waitFor();
    check(
      /юго-запад.*3-й этаж/s.test(
        await page.getByTestId("passport-saved-scope").innerText(),
      ),
      "R6: saved scope remains visible after submission",
    );
    const passportView = page.locator(".passport-view");
    check(
      (await passportView.innerText()).split("Перепроверим ближе к поездке")
        .length -
        1 ===
        1,
      "R6: future review copy occurs exactly once in the guest passport",
    );
    await bothWidths(page, "r6-created", page.getByTestId("passport-recheck"));
    const login = await api("/api/hotelier/login", {
      username: "hotelier",
      password: "hotelier123",
    });
    token = login.token; // Memory only: no storageState, file or auth data in the report.
    const confirmed = await api(
      `/api/passports/${encodeURIComponent(created.id)}/action`,
      { action: "confirm", room_number: "305", text: hotelComment },
      true,
    );
    check(
      confirmed.status === "confirmed" && confirmed.room_number === "305",
      "R6: cabinet confirmation preserves room 305 despite a future TTL",
    );
    check(
      confirmed.items.every((item) => item.status === "confirmed"),
      "R6: accepted expectations are confirmed",
    );
    await page.reload();
    await page.getByTestId("passport-hotel-reply").waitFor();
    check(
      (await passportView.innerText()).includes(hotelComment) &&
        (await passportView.innerText()).includes("Номер 305"),
      "R6: guest reload shows the room and the operator comment",
    );
    check(
      (await page.locator(".passport-status h3").innerText()).includes(
        "Отель подтвердил ожидания",
      ),
      "R6: actual agreement has a meaningful heading",
    );
    check(
      (await page.locator(".passport-status").innerText()).includes(
        "Демо-бронь",
      ),
      "R6: the separate mock-booking badge remains visible",
    );
    const mapItem = page
      .locator(".passport-item")
      .filter({ hasText: "Проверено по карте" })
      .first();
    check(
      (await mapItem.count()) > 0 &&
        !/критично|critical/i.test(await mapItem.innerText()),
      "R6: map information is separate from hotel guarantees and critical risk",
    );
    check(
      (await passportView.innerText()).split("Перепроверим ближе к поездке")
        .length -
        1 ===
        1,
      "R6: confirmation does not duplicate the review deadline",
    );
    await bothWidths(
      page,
      "r6-confirmed",
      page.getByTestId("passport-hotel-reply"),
    );
    report.stages.R6 = {
      status: "passed",
      hotel_id: cascade.hotel.id,
      expected_position: { facade: "SW", floor: 3 },
      room_number: "305",
      future_review_by: reviews[0].review_by,
      confirmed_items: confirmed.items.length,
      operator_comment: hotelComment,
    };
    await save();

    stage = "R11";
    const stroller = await searchUI(page, query.stroller);
    check(
      stroller.parsed.must.some(
        (criterion) => criterion.characteristic_id === "accessible_route",
      ),
      "R11: stroller adds accessible_route as a must-have",
    );
    const catalogue = await api("/api/hotels");
    const primorskaya = catalogue.find((hotel) =>
      /приморская/i.test(hotel.name),
    );
    check(!!primorskaya, "R11: Primorskaya is in the actual catalogue");
    const allStroller = await api("/api/search", {
      text: query.stroller,
      parsed: stroller.parsed,
      ...trip,
      limit: 250,
      compact: true,
    });
    const primHit = allStroller.results.find(
      (hit) => hit.hotel.id === primorskaya.id,
    );
    const primExact = await api(
      `/api/hotels/${encodeURIComponent(primorskaya.id)}/evaluate`,
      allStroller.parsed,
    );
    const accessible = primExact.verdicts.find(
      (verdict) => verdict.characteristic_id === "accessible_route",
    );
    report.stages.R11 = {
      status: "running",
      hotel_id: primorskaya.id,
      compact_returned: allStroller.results.length,
      compact_total: allStroller.total,
      in_compact_prefix: !!primHit,
      exact_verdict: accessible
        ? {
            status: accessible.status,
            text: accessible.text,
            fact_ids: accessible.fact_ids,
            evidence_refs: accessible.evidence_refs,
          }
        : null,
      exact_route_facts: primExact.facts
        .filter((fact) =>
          ["accessible_route", "beach_walk"].includes(fact.characteristic_id),
        )
        .map((fact) => ({
          id: fact.id,
          characteristic_id: fact.characteristic_id,
          status: fact.status,
          value: fact.value,
          supporting_claim_ids: fact.supporting_claim_ids,
          evidence_refs: fact.evidence.map((evidence) => evidence.id),
        })),
    };
    await save();
    check(
      accessible?.status === "no",
      "R11: exact evaluation of known stairs at Primorskaya is no, not unknown",
    );
    if (primHit)
      check(
        primHit.verdicts.find(
          (verdict) => verdict.characteristic_id === "accessible_route",
        )?.status === accessible.status,
        "R11: compact search and exact evaluation agree when the hotel is in the prefix",
      );
    await page.goto(
      new URL(`/hotel/${encodeURIComponent(primorskaya.id)}`, origin).href,
    );
    await page.locator(".hotel-fit .verdict-list").waitFor();
    const routeVerdict = page
      .locator(".hotel-fit .verdict-list > li")
      .filter({
        has: page.locator("strong").filter({ hasText: accessible.label }),
      })
      .first();
    check(
      /не подходит/i.test(await routeVerdict.innerText()),
      "R11: the actual hotel UI shows the route rejection",
    );
    await bothWidths(page, "r11-stroller", routeVerdict);
    report.stages.R11 = {
      ...report.stages.R11,
      status: "passed",
      hotel_id: primorskaya.id,
      accessible_route: accessible.status,
      parsed_must: stroller.parsed.must.map(
        (criterion) => criterion.characteristic_id,
      ),
    };
    await save();

    stage = "R12";
    const group = await searchUI(page, query.group);
    check(
      group.parsed.rooms?.length === 2,
      "R12: actual company search creates two personal requests",
    );
    const groupHit =
      group.results.find(
        (hit) =>
          /cascade/i.test(hit.hotel.name) &&
          hit.room_plan?.[1]?.profile?.scope.facade,
      ) ||
      group.results.find((hit) => hit.room_plan?.[1]?.profile?.scope.facade);
    check(
      !!groupHit,
      "R12: the second person has an actual selected room profile",
    );
    const selected = groupHit.room_plan[1].profile.scope;
    const link = page
      .getByTestId(`hotel-card-${groupHit.hotel.id}`)
      .getByTestId("open-group-room-1");
    const href = new URL(await link.getAttribute("href"), origin);
    check(
      href.searchParams.get("room_side") === selected.facade &&
        Number(href.searchParams.get("room_floor")) === selected.floor_min &&
        Number(href.searchParams.get("room_month")) === selected.month &&
        href.searchParams.get("room_request") === "1",
      "R12: the second person's link carries its selected facade, floor, month and request index",
    );
    await link.click();
    await page.getByTestId("room-lab").waitFor();
    const lab = page.getByTestId("room-lab");
    if ((await lab.getAttribute("data-open")) !== "true")
      await lab.locator(":scope > details > summary").click();
    check(
      (await page.getByTestId("room-lab-facade").inputValue()) ===
        selected.facade &&
        Number(await page.getByTestId("room-lab-floor").inputValue()) ===
          selected.floor_min &&
        Number(await page.getByTestId("room-lab-month").inputValue()) ===
          selected.month,
      "R12: RoomLab displays the selected second-person position",
    );
    check(
      (await page.getByTestId("room-lab-person").innerText()).includes(
        group.parsed.rooms[1].label,
      ),
      "R12: RoomLab keeps the correct person label",
    );
    await bothWidths(
      page,
      "r12-personal-window",
      page.getByTestId("room-lab-facade"),
    );
    report.stages.R12 = {
      status: "passed",
      hotel_id: groupHit.hotel.id,
      person_index: 1,
      person_label: group.parsed.rooms[1].label,
      position: {
        facade: selected.facade,
        floor: selected.floor_min,
        month: selected.month,
      },
    };
    await save();

    stage = "R5";
    await page.goto(new URL("/studio", origin).href);
    await page.getByTestId("studio-description").fill(query.studio);
    const compilation = await uiPost(page, "/api/characteristics/compile", () =>
      page.getByTestId("studio-compile").click(),
    );
    const spec = compilation.spec;
    compiledId = spec.id;
    check(
      spec.search.field === "distance_m" &&
        spec.search.default_op === "lte" &&
        spec.search.default_value === 300,
      "R5: compile retains the requested 300-metre criterion",
    );
    const dry = await uiPost(page, "/api/characteristics/dry-run", () =>
      page.getByTestId("studio-dry-run").click(),
    );
    check(
      dry.results.length === 3 && dry.processed === 3,
      "R5: dry-run uses the actual three-hotel sample",
    );
    const sample = dry.results.map((row) => {
      const values = row.claims
        .filter(
          (claim) =>
            claim.characteristic_id === spec.id &&
            claim.evidence?.length &&
            (!claim.valid_until || Date.parse(claim.valid_until) > Date.now()),
        )
        .map((claim) => claim.value);
      const distances = values
        .map((value) => value?.distance_m)
        .filter((value) => typeof value === "number");
      const expected =
        distances.length > 0 &&
        values.every(
          (value) =>
            value.available !== false &&
            typeof value.distance_m === "number" &&
            value.distance_m <= 300,
        );
      return {
        hotel_id: row.hotel_id,
        distance_m: distances[0] ?? null,
        expected,
        matched: row.matched,
        unknown: distances.length === 0,
        claim_ids: row.claims.map((claim) => claim.id),
        evidence_refs: row.claims
          .flatMap((claim) => claim.evidence || [])
          .map((evidence) => evidence.id),
      };
    });
    report.stages.R5 = {
      status: "running",
      characteristic_id: spec.id,
      threshold_m: 300,
      sample,
      matched_count: dry.matched_count,
    };
    await save();
    for (const row of sample)
      check(
        row.matched === row.expected,
        "R5: sample matched agrees with its supported claim distance",
      );
    check(
      dry.matched_count === sample.filter((row) => row.expected).length,
      "R5: matched_count is the number of actual matching samples",
    );
    await page.getByTestId("studio-sample-summary").waitFor();
    check(
      (await page.getByTestId("studio-sample-summary").innerText()).includes(
        `подходит ${dry.matched_count} из 3`,
      ),
      "R5: the Studio UI shows the actual matched sample count",
    );
    const publication = await uiPost(page, "/api/characteristics/publish", () =>
      page.getByTestId("studio-publish").click(),
    );
    const end = Date.now() + 180000;
    let run;
    do {
      run = await api(`/api/runs/${encodeURIComponent(publication.run_id)}`);
      check(run.status !== "failed", "R5: actual backfill has not failed");
      if (run.status === "completed") break;
      await delay(1000);
    } while (Date.now() < end);
    check(
      run?.status === "completed",
      "R5: actual publication backfill completes",
    );
    const publishedUI = await searchUI(page, query.studio);
    check(
      publishedUI.parsed.must.some(
        (criterion) =>
          criterion.characteristic_id === spec.id &&
          criterion.field === spec.search.field &&
          criterion.op === spec.search.default_op &&
          criterion.value === spec.search.default_value,
      ),
      "R5: the UI search uses the same published criterion",
    );
    const publishedSearch = await api("/api/search", {
      text: query.studio,
      parsed: publishedUI.parsed,
      ...trip,
      limit: 250,
      compact: true,
    });
    for (const row of sample) {
      const hit = publishedSearch.results.find(
        (item) => item.hotel.id === row.hotel_id,
      );
      // Compact search is a bounded prefix, even when limit=250. A missing
      // hotel has no search verdict; it is not an unknown characteristic.
      const detail = await api(
        `/api/hotels/${encodeURIComponent(row.hotel_id)}/evaluate`,
        publishedUI.parsed,
      );
      const verdict = allVerdicts(detail).find(
        (item) => item.characteristic_id === spec.id,
      );
      row.in_compact_prefix = !!hit;
      row.exact_status = verdict?.status || null;
      report.stages.R5.sample = sample;
      await save();
      check(
        detail.hotel.id === row.hotel_id && !!verdict,
        "R5: exact evaluation retains the requested preview hotel and criterion",
      );
      if (hit)
        check(
          allVerdicts(hit).find((item) => item.characteristic_id === spec.id)
            ?.status === verdict.status,
          "R5: compact search and exact evaluation agree for sample hotels present in the prefix",
        );
      if (row.unknown)
        check(
          verdict.status === "unknown",
          "R5: unknown preview data remains unknown after publication",
        );
      else
        check(
          row.expected
            ? ["yes", "partial"].includes(verdict.status)
            : verdict.status === "no",
          "R5: published exact matching agrees with the same preview criterion",
        );
      const facts = detail.facts.filter(
        (fact) => fact.characteristic_id === spec.id,
      );
      if (row.distance_m !== null)
        check(
          facts.some((fact) => fact.value?.distance_m === row.distance_m),
          "R5: preview and published facts retain the same measured distance",
        );
    }
    const unknownPublished = publishedSearch.results
      .flatMap((hit) => allVerdicts(hit))
      .filter(
        (verdict) =>
          verdict.characteristic_id === spec.id && verdict.status === "unknown",
      ).length;
    const unknownHit = publishedSearch.results.find((hit) =>
      allVerdicts(hit).some(
        (verdict) =>
          verdict.characteristic_id === spec.id && verdict.status === "unknown",
      ),
    );
    let unknownInputVerified = sample.some((row) => row.unknown);
    if (unknownHit) {
      const detail = await api(
        `/api/hotels/${encodeURIComponent(unknownHit.hotel.id)}`,
      );
      const facts = detail.facts.filter(
        (fact) => fact.characteristic_id === spec.id,
      );
      check(
        facts.length === 0 ||
          facts.some(
            (fact) =>
              fact.status === "unknown" ||
              fact.value === null ||
              typeof fact.value?.distance_m !== "number",
          ),
        "R5: an unknown published verdict has missing input, not an invented negative distance",
      );
      unknownInputVerified = true;
    }
    report.stages.R5 = {
      status: "passed",
      characteristic_id: spec.id,
      threshold_m: 300,
      sample,
      matched_count: dry.matched_count,
      backfill_status: run.status,
      published_unknown_count: unknownPublished,
      unknown_input_verified: unknownInputVerified,
      unknown_coverage_note: unknownInputVerified
        ? "Checked against actual missing input"
        : "This test copy returned no missing-distance input; no synthetic observation was added",
    };
    await save();
    check(
      report.forbidden_mutations.length === 0,
      "No unapproved write was attempted",
    );
    check(
      report.page_errors.length === 0,
      "Actual guest pages have no uncaught JavaScript errors",
    );
    report.status = "passed";
    return report;
  } catch (error) {
    report.status = "failed";
    report.errors.push({ stage, error: safeError(error) });
    if (failurePage && !failurePage.isClosed()) {
      try {
        const file = `panel-r3-guest-failure-${stage.toLowerCase()}.png`;
        await failurePage.screenshot({
          path: resolve(output, file),
          fullPage: false,
        });
        report.captures.push({
          name: `failure-${stage}`,
          file,
          width: failurePage.viewportSize().width,
          failure: true,
        });
      } catch {
        /* Preserve the original assertion even if the browser closed. */
      }
    }
    throw new Error(`R3 guest actions failed at ${stage}: ${safeError(error)}`);
  } finally {
    if (token) {
      try {
        await api("/api/hotelier/logout", {}, true);
      } catch {
        /* Best-effort revocation of this memory-only session. */
      }
      token = null;
    }
    for (const context of contexts) await context.close().catch(() => {});
    if (browser) await closeSmokeBrowser(browser);
    report.finished_at = new Date().toISOString();
    await save();
  }
}

if (
  process.argv[1] &&
  resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  runPanelR3GuestActions({
    base: process.env.OKNO_BASE_URL || "http://localhost:8081",
    artifacts: process.env.OKNO_PANEL_R3_ARTIFACTS,
  })
    .then((report) => {
      console.log(
        JSON.stringify({
          status: report.status,
          test_copy: true,
          checks: report.checks.length,
          captures: report.captures.length,
        }),
      );
      process.exit(0);
    })
    .catch((error) => {
      console.error(safeError(error));
      process.exit(1);
    });
}
