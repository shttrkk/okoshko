/** Real-browser, same-API recording. No application/API response mocking or result substitution. */
import { createRequire } from "node:module";
import {
  existsSync,
  mkdirSync,
  renameSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { resolve, dirname } from "node:path";
import { execFileSync } from "node:child_process";
import assert from "node:assert/strict";
import type { Frame } from "../demo/node_modules/playwright/index";
import type {
  SearchResult,
  HotelDetail,
  Passport,
  Question,
  Answer,
  CompileResult,
  DryResult,
  PublishResult,
  AgentRun,
  ChangeEvent,
  Json,
} from "../frontend/src/types";

let cleanupBrowser: (() => Promise<void>) | undefined;
async function main() {
  const project = existsSync(resolve(process.cwd(), "demo/package.json"))
    ? process.cwd()
    : resolve(process.cwd(), "..");
  assert(
    existsSync(resolve(project, "demo/package.json")),
    "Run from the repository root or demo directory.",
  );
  const requireDemo = createRequire(resolve(project, "demo/package.json"));
  const { chromium } = requireDemo(
    "playwright",
  ) as typeof import("../demo/node_modules/playwright/index");
  const base = (process.env.OKNO_BASE_URL || "http://localhost:8080").replace(
    /\/$/,
    "",
  );
  const checkOnly = process.argv.includes("--check");
  const fast = checkOnly || process.argv.includes("--fast");
  const query = "тихий номер с видом на закат недалеко от моря";
  const checkIn = "2027-09-14",
    checkOut = "2027-09-21";
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  const recordingDir = resolve(project, "demo/recordings", stamp);
  const output = resolve(
    project,
    process.env.OKNO_DEMO_OUTPUT || "demo/okno-demo.mp4",
  );
  mkdirSync(recordingDir, { recursive: true });
  const sleep = (ms: number) =>
    new Promise<void>((done) => setTimeout(done, ms));

  async function api<T>(path: string, body?: unknown): Promise<T> {
    const response = await fetch(base + path, {
      method: body === undefined ? "GET" : "POST",
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(30000),
    });
    const value = await response.json();
    if (!response.ok)
      throw new Error(
        `${response.status} ${path}: ${JSON.stringify(value).slice(0, 400)}`,
      );
    return value as T;
  }
  const health = await api<{ status: string }>("/health");
  assert.equal(health.status, "ok");
  const status = await api<{
    hotel_count: number;
    characteristic_count: number;
    mode: string;
  }>("/api/status");
  const search = await api<SearchResult>("/api/search", {
    text: query,
    check_in: checkIn,
    check_out: checkOut,
    limit: 40,
  });
  const heroes = search.results
    .filter((hit) => hit.group === "all")
    .slice(0, 3);
  assert.equal(
    heroes.length,
    3,
    "Need three real API matches. Reset the demo database after a previously simulated building.",
  );
  for (const hit of heroes)
    assert(
      hit.verdicts.some(
        (v) => v.characteristic_id === "quiet_night" && v.synthetic,
      ),
      "The demo quiet claim must remain visibly synthetic.",
    );
  const chosen =
    heroes.find((hit) => hit.hotel.id === process.env.OKNO_DEMO_HOTEL) ||
    heroes[0];
  const hero = chosen.hotel;
  const detail = await api<HotelDetail>(
    `/api/hotels/${encodeURIComponent(hero.id)}/evaluate`,
    search.parsed,
  );
  const sunset = detail.verdicts.find(
    (v) => v.characteristic_id === "sunset_view",
  );
  const roomFact = detail.facts.find(
    (f) => sunset?.fact_ids.includes(f.id) && f.scope.facade,
  );
  const facade = roomFact?.scope.facade || detail.geo.facades?.[0]?.code;
  const floor =
    roomFact?.scope.floor_min ||
    detail.geo.floors?.modeled_count ||
    hero.floors ||
    1;
  const roomNumber = `${floor}12`; // Explicitly fictional demo-room input, never inventory.
  assert(detail.geo.horizon?.length, "Need the actual saved horizon profile.");
  assert(
    detail.geo.route?.features.length,
    "Need the actual saved walking route.",
  );
  assert(detail.geo.facades?.length, "Need real facade geometries.");
  const existingChanges = await api<ChangeEvent[]>("/api/changes");
  if (
    !checkOnly &&
    existingChanges.some((change) =>
      change.affected_hotel_ids.includes(hero.id),
    )
  )
    throw new Error(
      `A building scenario already exists for ${hero.id}. Record against a fresh local demo database; this script never deletes state.`,
    );
  if (!checkOnly) {
    // Playwright 1.56.1 records with an ffmpeg executable in its own cache.
    // Reuse the installed system binary; never download recording binaries.
    const installedFfmpeg = execFileSync(
      process.platform === "win32" ? "where" : "which",
      ["ffmpeg"],
      { encoding: "utf8" },
    )
      .trim()
      .split(/\r?\n/)[0];
    execFileSync(installedFfmpeg, ["-version"], { stdio: "ignore" });
    const core = dirname(requireDemo.resolve("playwright-core/package.json"));
    const { registry } = requireDemo(resolve(core, "lib/server/registry/index.js"));
    const recordingFfmpeg = registry.findExecutable("ffmpeg").executablePath();
    if (!existsSync(recordingFfmpeg)) {
      mkdirSync(dirname(recordingFfmpeg), { recursive: true });
      symlinkSync(installedFfmpeg, recordingFfmpeg);
    }
  }

  const candidates = [
    process.env.OKNO_BROWSER_EXECUTABLE,
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    chromium.executablePath(),
  ].filter((path): path is string => !!path);
  const executablePath = candidates.find(existsSync);
  if (!executablePath)
    throw new Error(
      "Set OKNO_BROWSER_EXECUTABLE to an already installed Chrome/Chromium binary. No browser is downloaded at recording time.",
    );
  const browser = await chromium.launch({
    headless: process.env.OKNO_HEADLESS !== "false",
    executablePath,
    args: ["--use-angle=metal"],
  });
  cleanupBrowser = () => browser.close();
  const context = await browser.newContext({
    viewport: { width: 1920, height: 1080 },
    deviceScaleFactor: 1,
    ...(checkOnly
      ? {}
      : {
          recordVideo: {
            dir: recordingDir,
            size: { width: 1920, height: 1080 },
          },
        }),
  });
  await context.addInitScript(() => {
    try {
      localStorage.setItem("okno-theme", "light");
    } catch {
      /* parent recording document has no storage origin */
    }
  });
  const blockedExternal: string[] = [];
  const externalImages: string[] = [];
  await context.route("**/*", (route) => {
    const url = new URL(route.request().url());
    if (
      ["http:", "https:"].includes(url.protocol) &&
      url.origin !== new URL(base).origin
    ) {
      // Linked hotel photos are the only allowed outside content; everything
      // else (APIs, tiles, scripts) must stay local.
      if (route.request().resourceType() === "image") {
        externalImages.push(url.href);
        return route.continue();
      }
      blockedExternal.push(url.href);
      return route.abort();
    }
    return route.continue();
  });
  const page = await context.newPage();
  if (process.platform === "darwin" && process.env.OKNO_HEADLESS !== "false") {
    // Installed Chrome's macOS headless window retains an 87px browser inset.
    // Device metrics alone fix screenshots but leave screencast frames cropped.
    const cdp = await context.newCDPSession(page);
    const { windowId } = await cdp.send("Browser.getWindowForTarget");
    await cdp.send("Browser.setWindowBounds", {
      windowId,
      bounds: { width: 1920, height: 1167 },
    });
    await cdp.detach();
  }
  page.setDefaultTimeout(18000);
  page.setDefaultNavigationTimeout(30000);
  const errors: string[] = [],
    failures: { status: number; url: string }[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("response", (response) => {
    if (response.status() >= 400)
      failures.push({ status: response.status(), url: response.url() });
  });
  // The application runs untouched in a full-width iframe. The 112px subtitle
  // strip is outside its viewport, so it cannot cover synthetic badges or facts.
  const shell = `<!doctype html><html lang="ru"><head><meta charset="utf-8"><style>html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#1c2a25;font-family:Arial,sans-serif}iframe{position:absolute;inset:0;width:100%;height:calc(100% - 112px);border:0;background:#f5f2e9}.caption{position:absolute;left:0;right:0;bottom:0;height:112px;display:grid;grid-template-columns:145px 610px 1fr 90px;gap:26px;align-items:center;padding:0 54px;box-sizing:border-box;border-top:3px solid #ed6c49;color:#f5f1e6}.chapter{font-size:12px;letter-spacing:2px;color:#e29174}.brand{font-size:24px;font-weight:700;letter-spacing:-1px;color:#f5f1e6;margin-top:7px}h1{font-size:27px;font-weight:400;letter-spacing:-.8px;margin:0;line-height:1.25}.subtitle{font-size:18px;line-height:1.4;color:#ccd3c7}.timer{font-size:15px;color:#99ad9c;align-self:center;text-align:right}</style></head><body><iframe id="okno-application" name="okno-application" title="О!кно — живое приложение" src="${base}"></iframe><div class="caption"><div><div class="chapter" id="chapter">ДЕМО-СЦЕНАРИЙ</div><div class="brand">о<span style="color:#ed6c49">!</span>кно</div></div><h1 id="scene-heading">Ваше море. Ваш закат. Ваше окно.</h1><div class="subtitle" id="scene-subtitle">Маша ищет отдых в Сочи. Важно, чтобы совпало главное.</div><div class="timer" id="timer">0:00</div></div></body></html>`;
  await page.route(`${base}/__okno-recording__`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "text/html; charset=utf-8",
      body: shell,
    }),
  );
  await page.goto(`${base}/__okno-recording__`, {
    waitUntil: "domcontentloaded",
  });

  await page
    .frameLocator("#okno-application")
    .locator(".site-header")
    .waitFor();
  const iframe = await page.locator("#okno-application").elementHandle();
  const app: Frame = (await iframe!.contentFrame())!;
  assert(app, "The real application frame must be attached.");
  const started = Date.now();
  await page.evaluate(() => {
    const start = Date.now();
    setInterval(() => {
      const seconds = Math.floor((Date.now() - start) / 1000);
      document.getElementById("timer")!.textContent =
        `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
    }, 250);
  });
  const report: Record<string, unknown> = {
    started_at: new Date().toISOString(),
    base,
    check_only: checkOnly,
    viewport: { width: 1920, height: 1080 },
    browser_version: browser.version(),
    hero: {
      id: hero.id,
      name: hero.name,
      facade,
      floor,
      synthetic_quiet: true,
    },
    initial_status: status,
    initial_search_ms: search.latency_ms,
    stages: [],
  };
  let scene = 0,
    passport: Passport | undefined,
    question: Question | undefined,
    published: PublishResult | undefined;
  let failure: unknown, rawPath: string | undefined;
  async function caption(title: string, subtitle: string) {
    scene++;
    await page.evaluate(
      ({ title, subtitle, scene }) => {
        document.getElementById("chapter")!.textContent =
          `${String(scene).padStart(2, "0")} / СЦЕНАРИЙ МАШИ`;
        document.getElementById("scene-heading")!.textContent = title;
        document.getElementById("scene-subtitle")!.textContent = subtitle;
      },
      { title, subtitle, scene },
    );
  }
  async function navigate(path: string) {
    await app.goto(base + path, { waitUntil: "domcontentloaded" });
    await app.locator(".site-header").waitFor();
  }
  async function response<T>(
    path: string,
    action: () => Promise<unknown>,
  ): Promise<T> {
    const pending = page.waitForResponse(
      (r) =>
        new URL(r.url()).pathname === path && r.request().method() === "POST",
      { timeout: 60000 },
    );
    await action();
    const result = await pending;
    if (!result.ok())
      throw new Error(
        `${result.status()} ${path}: ${(await result.text()).slice(0, 400)}`,
      );
    return (await result.json()) as T;
  }
  async function stage(
    title: string,
    subtitle: string,
    budget: number,
    action: () => Promise<void>,
  ) {
    await caption(title, subtitle);
    const start = Date.now();
    await action();
    if (!fast) await sleep(Math.max(0, budget - (Date.now() - start)));
    const duration = Date.now() - start;
    (report.stages as unknown[]).push({
      scene,
      title,
      started_seconds: (start - started) / 1000,
      duration_seconds: duration / 1000,
    });
    await page.screenshot({
      path: resolve(recordingDir, `${String(scene).padStart(2, "0")}.png`),
    });
    console.log(`${scene}. ${title}: ${(duration / 1000).toFixed(1)}s`);
    // Preserve every real action even when the API is slow. The complete source
    // is retained; the final encoding may use a disclosed modest speed increase.
    if (!checkOnly && Date.now() - started > 176000)
      report.timing_warning = "Live UI exceeded the target scene budget";
  }
  try {
    await stage(
      "Три пожелания вместо четырнадцати вкладок",
      "Тишина, закат из окна и море рядом. Демонстрационный сценарий Маши.",
      6000,
      async () => {
        await app
          .locator('.hero-map[data-map-status="ready"]')
          .waitFor({ timeout: 30000 });
        await app.getByTestId("search-input").fill(query);
        await app.getByTestId("check-in").fill(checkIn);
        await app.getByTestId("check-out").fill(checkOut);
      },
    );
    await stage(
      "Важное стало проверяемыми условиями",
      "Три совпадения. Тишина в них — явно синтетический ответ; геометрия и маршрут рассчитаны по открытым данным.",
      20000,
      async () => {
        const result = await response<SearchResult>("/api/search", () =>
          app.getByTestId("search-submit").click(),
        );
        assert(result.results.filter((hit) => hit.group === "all").length >= 3);
        await app
          .locator('.search-map [data-map-status="ready"]')
          .waitFor({ timeout: 30000 });
        for (const hit of heroes) {
          const card = app.getByTestId(`hotel-card-${hit.hotel.id}`);
          await card.scrollIntoViewIfNeeded();
          assert((await card.locator(".badge.synthetic").count()) > 0);
          await app.getByTestId(`compare-${hit.hotel.id}`).click();
          if (!fast) await sleep(1300);
        }
        await app.getByTestId(`hotel-card-${hero.id}`).scrollIntoViewIfNeeded();
      },
    );
    await stage(
      "Да, но — с нужной стороны и этажа",
      "Раскроем источник, горизонт и чувствительность к неопределённым высотам. Monte Carlo не измеряет реальную точность.",
      24000,
      async () => {
        await app
          .getByTestId(`hotel-card-${hero.id}`)
          .locator(".hotel-name")
          .click();
        await app
          .locator(".hotel-fit .verdict-list")
          .waitFor({ timeout: 25000 });
        await app
          .locator(".hotel-fit .verdict-text")
          .filter({ hasText: "Закат из окна" })
          .click();
        await app.getByRole("dialog").waitFor();
        assert.match(
          await app.getByRole("dialog").innerText(),
          /Следующая проверка \/ TTL/,
        );
        if (!fast) await sleep(3500);
        await app.getByRole("button", { name: "Закрыть", exact: true }).click();
        await app.getByTestId("show-calculation").click();
        if (facade) await app.getByTestId("geo-facade").selectOption(facade);
        await app.getByTestId("geo-floor").fill(String(floor));
        await app.getByTestId("geo-month").selectOption("9");
        await app
          .getByRole("dialog")
          .locator('[data-map-status="ready"]')
          .waitFor({ timeout: 25000 });
        if (!fast) await sleep(1800);
        if (floor > 1) {
          await app.getByTestId("geo-floor").fill(String(floor - 1));
          await app.getByTestId("geo-month").selectOption("12");
          if (!fast) await sleep(1700);
          await app.getByTestId("geo-floor").fill(String(floor));
          await app.getByTestId("geo-month").selectOption("9");
        }
        await app
          .getByRole("dialog")
          .locator(".horizon-section")
          .scrollIntoViewIfNeeded();
        if (!fast) await sleep(1800);
        await app.getByRole("button", { name: "Закрыть", exact: true }).click();
      },
    );
    await stage(
      "Неизвестное — вопрос, а не «нет»",
      "Сравним три отеля. Шумоизоляцию нельзя подтвердить геокартой — спросим отель.",
      12000,
      async () => {
        await app.getByRole("link", { name: /^Сравнение/ }).click();
        await app.locator(".comparison-table").waitFor({ timeout: 25000 });
        assert.equal(await app.locator(".compare-hotel-head").count(), 3);
        if (!fast) await sleep(1700);
        await app.getByLabel("Только различия").uncheck();
        const row = app
          .locator(".comparison-table tbody tr")
          .filter({ hasText: "Шумоизоляция" });
        await row.scrollIntoViewIfNeeded();
        const column = heroes.findIndex((hit) => hit.hotel.id === hero.id);
        question = await response<Question>("/api/questions", () =>
          row
            .locator("td")
            .nth(column)
            .getByRole("button", { name: "Спросить", exact: true })
            .first()
            .click(),
        );
        report.question_id = question.id;
      },
    );
    await stage(
      "Пожелания не потеряются в комментарии",
      "Пока без дат: предварительно согласуем один номер. Перед поездкой нужно перепроверить условия, даты и реальное наличие.",
      8000,
      async () => {
        await navigate(`/booking/${encodeURIComponent(hero.id)}`);
        await app.getByTestId("passport-submit").waitFor({ timeout: 25000 });
        await app.getByTestId("passport-clear-dates").click();
        assert.equal(
          await app
            .getByLabel("Заезд в паспорте", { exact: true })
            .inputValue(),
          "",
        );
        passport = await response<Passport>("/api/passports", () =>
          app.getByTestId("passport-submit").click(),
        );
        assert(passport.synthetic_booking);
        assert.equal(passport.check_in, null);
        assert.equal(passport.check_out, null);
        report.passport_mode = "preliminary_without_dates";
        assert.equal(passport.items.filter((item) => item.critical).length, 3);
        report.passport_id = passport.id;
        await app.locator(".passport-view").scrollIntoViewIfNeeded();
      },
    );
    await stage(
      "Отель отвечает один раз — по полям",
      "Ответ симулятора и спрос явно синтетические. Цитаты проверяем до записи фактов; двойные окна не доказывают шумоизоляцию.",
      22000,
      async () => {
        await navigate("/hotelier");
        if (await app.getByTestId("hotelier-login").count())
          await app.getByTestId("hotelier-login").click();
        await app.getByTestId("hotelier-hotel").waitFor({ timeout: 25000 });
        await app.getByTestId("hotelier-hotel").selectOption(hero.id);
        await app
          .locator(".hotelier-summary h3")
          .filter({ hasText: hero.name })
          .waitFor({ timeout: 25000 });
        await app.getByTestId("hotelier-question").waitFor({ timeout: 25000 });
        if (await app.getByTestId("hotelier-question-select").count())
          await app
            .getByTestId("hotelier-question-select")
            .selectOption(question!.id);
        const answer = await response<Answer>(
          `/api/questions/${question!.id}/simulate`,
          () => app.getByTestId("answer-simulate").click(),
        );
        assert(answer.synthetic);
        assert(answer.updates.length > 0);
        assert(
          answer.updates.every((update) =>
            answer.raw_text.includes(update.quote),
          ),
        );
        await app.getByTestId("answer-fields").scrollIntoViewIfNeeded();
        if (!fast) await sleep(5000);
        const confirmed = await response<Answer>(
          `/api/answers/${answer.id}/confirm`,
          () => app.getByTestId("answer-confirm").click(),
        );
        assert(confirmed.confirmed);
        report.answer_claim_count = confirmed.created_claim_ids.length;
        const card = app
          .locator(".passport-view")
          .filter({ hasText: passport!.id.slice(0, 8).toUpperCase() });
        await card.getByLabel("Номер", { exact: true }).fill(roomNumber);
        await card
          .getByLabel("Комментарий или альтернатива")
          .fill(
            `Демо: фасад ${facade}, этаж ${floor}. Наличие реального номера не проверялось.`,
          );
        passport = await response<Passport>(
          `/api/passports/${passport!.id}/action`,
          () =>
            card
              .getByRole("button", { name: "Подтвердить", exact: true })
              .click(),
        );
        assert.equal(passport.status, "confirmed");
        assert.equal(passport.room_number, roomNumber);
        await card.locator(".passport-status").scrollIntoViewIfNeeded();
      },
    );
    await stage(
      "Договорились о конкретном номере",
      "Предварительное учебное подтверждение появилось в паспорте. Даты и реальное наличие ещё нужно согласовать.",
      5000,
      async () => {
        await navigate(
          `/booking/${encodeURIComponent(hero.id)}?passport=${passport!.id}`,
        );
        await app.locator(".passport-view").waitFor({ timeout: 25000 });
        await app.locator(".passport-view").scrollIntoViewIfNeeded();
        assert.match(
          await app.locator(".passport-view").innerText(),
          /Отель подтвердил/,
        );
      },
    );
    await stage(
      "Новый смысл — через спецификацию",
      "«Искупаться до завтрака»: маршрут до 10 минут и архивная вода от 20 °C. Проверка на трёх отелях перед публикацией.",
      17000,
      async () => {
        await navigate("/studio");
        const compiled = await response<CompileResult>(
          "/api/characteristics/compile",
          () => app.getByTestId("studio-compile").click(),
        );
        assert.equal(compiled.spec.id, "morning_swim");
        await app.locator(".compiled-spec").scrollIntoViewIfNeeded();
        if (!fast) await sleep(2000);
        const dry = await response<DryResult>(
          "/api/characteristics/dry-run",
          () => app.getByTestId("studio-dry-run").click(),
        );
        assert(dry.valid);
        assert.equal(dry.results.length, 3);
        report.dry_run = dry.results.map((result) => ({
          hotel_id: result.hotel_id,
          claims: result.claims.length,
          warnings: result.warnings,
        }));
        await app.locator(".dry-results").scrollIntoViewIfNeeded();
        if (!fast) await sleep(2500);
        if (checkOnly) return;
        published = await response<PublishResult>(
          "/api/characteristics/publish",
          () => app.getByTestId("studio-publish").click(),
        );
        report.backfill_run_id = published.run_id;
        await app.locator(".published-result").scrollIntoViewIfNeeded();
        if (!fast) await sleep(1800);
        report.visible_backfill_progress = await app
          .locator(".published-result progress")
          .getAttribute("value");
        await app
          .locator(".published-result")
          .getByRole("link", { name: /Добавить новый чип/ })
          .click();
        await app.locator(".criteria-panel").waitFor({ timeout: 25000 });
        await app
          .getByLabel("Добавить условие", { exact: true })
          .selectOption("morning_swim");
        const result = await response<SearchResult>("/api/search", () =>
          app.getByRole("button", { name: "Применить", exact: true }).click(),
        );
        assert(
          result.parsed.nice.some(
            (criterion) => criterion.characteristic_id === "morning_swim",
          ),
        );
      },
    );
    await stage(
      "У каждого факта есть путь",
      "Живой SSE: план → инструменты → доказательства → разрешение фактов. Без скрытого исполнения произвольного кода.",
      10000,
      async () => {
        await navigate("/agent");
        await app.locator(".operations-toolbar select").selectOption(hero.id);
        const run = await response<AgentRun>("/api/runs", () =>
          app.getByTestId("agent-run").click(),
        );
        await app.locator(".agent-step").first().waitFor({ timeout: 25000 });
        report.agent_run_id = run.id;
        report.agent_visible_steps = await app.locator(".agent-step").count();
      },
    );
    if (!checkOnly)
      await stage(
        "Мир меняется — паспорт предупреждает",
        "Синтетический 16-этажный дом реально включается в расчёт. Сравним сохранённые виды и уведомим затронутый паспорт.",
        22000,
        async () => {
          await navigate("/changes");
          await app.locator("#change-hotel").selectOption(hero.id);
          const event = await response<ChangeEvent>(
            "/api/changes/simulate",
            () => app.getByTestId("simulate-change").click(),
          );
          assert(event.synthetic);
          assert(event.before.length && event.after.length);
          assert(
            event.notifications.some(
              (notification) => notification.passport_id === passport!.id,
            ),
            "The newly created passport must receive a real local notification.",
          );
          report.change_id = event.id;
          report.notifications = event.notifications.length;
          await app.locator(".change-map-pair").waitFor({ timeout: 30000 });
          await app
            .locator('.change-map-pair [data-map-status="ready"]')
            .nth(1)
            .waitFor({ timeout: 30000 });
          await app.locator(".change-map-pair").scrollIntoViewIfNeeded();
          if (!fast) await sleep(5000);
          await navigate(
            `/booking/${encodeURIComponent(hero.id)}?passport=${passport!.id}`,
          );
          await app.locator(".passport-view").waitFor({ timeout: 25000 });
          await app.locator(".passport-view").scrollIntoViewIfNeeded();
          assert.match(
            await app.locator(".passport-view").innerText(),
            /Ожидание под угрозой/,
          );
        },
      );
    const finalStatus = await api<typeof status>("/api/status");
    await stage(
      `${finalStatus.hotel_count} отелей. Проверяемые основания.`,
      "Покрытие, слабые метки и синтетические тесты показаны раздельно. Неизмеренная точность остаётся неизмеренной.",
      8000,
      async () => {
        const qualityResponse = page.waitForResponse(
          (result) =>
            new URL(result.url()).pathname === "/api/quality" && result.ok(),
          { timeout: 60000 },
        );
        await navigate("/quality");
        await app.locator(".quality-stats").waitFor({ timeout: 60000 });
        const quality = (await (await qualityResponse).json()) as Json;
        if (!fast) await sleep(6000);
        report.final_status = finalStatus;
        report.quality = {
          coverage: quality.coverage,
          manual_label_count: quality.manual_label_count,
          parser_accuracy: quality.parser_accuracy,
          ndcg_at_5: quality.ndcg_at_5,
        };
      },
    );
    assert.deepEqual(errors, [], "Uncaught application errors");
    assert.deepEqual(failures, [], "Failed local API or asset requests");
    assert.deepEqual(
      blockedExternal,
      [],
      "Unexpected external network dependencies",
    );
    report.status = "passed";
  } catch (error) {
    failure = error;
    report.status = "failed";
    report.error = String(error);
    await page
      .screenshot({ path: resolve(recordingDir, "failure.png") })
      .catch(() => {});
  } finally {
    const video = page.video();
    await context.close();
    if (video) {
      const source = await video.path();
      rawPath = resolve(recordingDir, "okno-demo-source.webm");
      renameSync(source, rawPath);
    }
    await browser.close();
    report.elapsed_seconds = (Date.now() - started) / 1000;
    report.browser_errors = errors;
    report.failed_requests = failures;
    report.blocked_external = blockedExternal;
    report.external_images = externalImages.length;
    report.raw_video = rawPath;
  }
  if (!failure && published) {
    try {
      let completed: AgentRun | undefined;
      for (let attempt = 0; attempt < 60; attempt++) {
        completed = await api<AgentRun>(`/api/runs/${published.run_id}`);
        if (completed.status !== "running") break;
        await sleep(10000);
      }
      assert.equal(
        completed?.status,
        "completed",
        "The published characteristic must complete durable backfill.",
      );
      const processed = Math.max(
        0,
        ...completed!.steps
          .map((step) => step.output.processed)
          .filter((value): value is number => typeof value === "number"),
      );
      assert.equal(
        processed,
        published.total,
        "Backfill must process the actual complete hotel catalog.",
      );
      report.completed_backfill = {
        run_id: published.run_id,
        status: completed!.status,
        processed,
        total: published.total,
      };
    } catch (error) {
      failure = error;
      report.status = "failed";
      report.error = String(error);
    }
  }
  if (!failure && !checkOnly && rawPath) {
    try {
      mkdirSync(dirname(output), { recursive: true });
      const sourceProbe = JSON.parse(
        execFileSync(
          "ffprobe",
          ["-v", "error", "-show_entries", "format=duration", "-of", "json", rawPath],
          { encoding: "utf8" },
        ),
      );
      const sourceDuration = Number(sourceProbe.format.duration);
      const playbackSpeed = Math.max(1, sourceDuration / 174);
      assert(
        playbackSpeed <= 1.35,
        "Source too long for readable automatic encoding; retain it for editing",
      );
      report.encoding = {
        source_duration_seconds: sourceDuration,
        playback_speed: playbackSpeed,
        final_hold_seconds: 4,
        cuts: [],
        note: "The complete source is retained. A modest speed increase preserves every action and loading state.",
      };
      execFileSync(
        "ffmpeg",
        [
          "-y",
          "-i",
          rawPath,
          "-vf",
          `setpts=PTS/${playbackSpeed},tpad=stop_mode=clone:stop_duration=4`,
          "-an",
          "-c:v",
          "libx264",
          "-preset",
          "medium",
          "-crf",
          "22",
          "-maxrate",
          "2M",
          "-bufsize",
          "4M",
          "-pix_fmt",
          "yuv420p",
          "-r",
          "30",
          "-movflags",
          "+faststart",
          output,
        ],
        { stdio: "ignore" },
      );
      const probe = JSON.parse(
        execFileSync(
          "ffprobe",
          [
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=width,height",
            "-of",
            "json",
            output,
          ],
          { encoding: "utf8" },
        ),
      );
      assert.equal(probe.streams[0]?.width, 1920);
      assert.equal(probe.streams[0]?.height, 1080);
      report.video = probe;
      report.mp4 = output;
      assert(
        Number(probe.format.duration) <= 180,
        "Video must be no longer than three minutes",
      );
    } catch (error) {
      failure = error;
      report.status = "failed";
      report.error = String(error);
    }
  }
  writeFileSync(
    resolve(recordingDir, "report.json"),
    JSON.stringify(report, null, 2) + "\n",
  );
  console.log(JSON.stringify(report, null, 2));
  if (failure) throw failure;
}
void main().catch(async (error) => {
  await cleanupBrowser?.();
  console.error(error);
  process.exitCode = 1;
});
