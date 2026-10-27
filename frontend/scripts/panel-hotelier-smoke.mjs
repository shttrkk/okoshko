import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const root = fileURLToPath(new URL("../..", import.meta.url));
const base = process.env.OKNO_BASE_URL || "http://localhost:8080";
const artifacts = resolve(
  process.env.OKNO_HOTELIER_ARTIFACTS || `${root}/runtime/panel-hotelier`,
);
const chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
await mkdir(artifacts, { recursive: true });
const report = {
  synthetic: true,
  shared_database_writes: 0,
  checks: [],
  captures: [],
  fixture_posts: [],
};
const check = (ok, name) => {
  assert.ok(ok, name);
  report.checks.push(name);
};
const hotel = {
  id: "panel-hotel",
  name: "Проверочный отель",
  address: "Сочи",
  lat: 43.58,
  lon: 39.72,
  tags: {},
  floors: 5,
};
const hotels = [
  { ...hotel, id: "brand-name-hotel", name: "Островок" },
  hotel,
  ...Array.from({ length: 245 }, (_, i) => ({
    ...hotel,
    id: `fixture-${i}`,
    name: `Проверочный объект ${i + 1}`,
  })),
];
const specs = [
  { id: "pets", name_ru: "С питомцем", scope: "hotel" },
  { id: "soundproofing", name_ru: "Шумоизоляция и окна", scope: "room_type" },
  {
    id: "evening_heat",
    name_ru: "Техническое старое название",
    display_name: "Вечернее солнце",
    scope: "facade",
  },
].map((s) => ({
  ...s,
  hotelier_answerable: true,
  category: "Номер",
  methods: [],
  ttl_days: 90,
  status: "active",
  description: "Проверочная характеристика",
}));
const scope = {
  type: "hotel",
  facade: null,
  floor_min: null,
  floor_max: null,
  room_type: null,
  month: null,
};
const at = "2026-10-27T12:00:00Z";
const fact = (cid, value, id = cid) => ({
  id,
  hotel_id: hotel.id,
  characteristic_id: cid,
  value,
  scope,
  confidence: value === null ? 0 : 0.85,
  status: value === null ? "unknown" : "computed",
  evidence: [],
  supporting_claim_ids: [],
  conflicting_claim_ids: [],
  synthetic: true,
  observed_at: at,
  valid_until: "2027-10-27T12:00:00Z",
});
const publicRun = {
  id: "public-search",
  kind: "search",
  hotel_id: null,
  status: "completed",
  started_at: at,
  llm_mode: "offline",
  steps: [
    {
      id: "public-step",
      run_id: "public-search",
      seq: 0,
      agent: "search",
      type: "decision",
      name: "Запрос гостя",
      rationale: "Личный текст скрыт; показан ход обработки запроса.",
      input: { summary: "Запрос гостя" },
      output: { total: 247 },
      evidence_refs: [],
      duration_ms: 3,
      mode: "offline",
      cache_hit: false,
    },
  ],
};
const collection = {
  ...publicRun,
  id: "collection",
  kind: "collect",
  hotel_id: hotel.id,
  steps: [
    {
      ...publicRun.steps[0],
      id: "collect-step",
      run_id: "collection",
      agent: "collector",
      name: "Проверены открытые данные",
      rationale: "SSE / TTL / claims are developer details",
      input: { hotel_id: hotel.id },
      output: { claim_count: 2 },
      evidence_refs: ["fixture-evidence"],
    },
  ],
};
const change = {
  id: "fixture-change",
  kind: "OSM_BUILDING_NEW",
  title: "Демо: новый дом изменил видимость",
  synthetic: true,
  detected_at: at,
  affected_hotel_ids: [hotel.id],
  before: [
    fact("evening_heat", { available: true }, "before"),
    fact("pets", { available: true, fee_rub_night: 500 }, "same-before"),
  ],
  after: [
    fact("evening_heat", { available: false }, "after"),
    fact("pets", { fee_rub_night: 500, available: true }, "same-after"),
  ],
  notifications: [],
  payload: { notification_count: 1 },
};
const compiledSpec = {
  ...specs[0],
  id: "nearby_cafe",
  name_ru: "Кафе рядом",
  description: "Расстояние до кафе по прямой",
  value_schema: { type: "object" },
  search: {
    kind: "numeric",
    field: "distance_m",
    default_op: "lte",
    default_value: 400,
  },
};
const compileResult = {
  spec: compiledSpec,
  yaml_text: "id: nearby_cafe",
  mode: "offline",
  plan: [
    {
      tool: "local_poi",
      params: { kind: "cafe" },
      rationale: "Расстояние по прямой",
    },
  ],
  warnings: ["Офлайн: детерминированный шаблон; LLM сеть не использовалась"],
};
const quality = {
  manual_label_count: 0,
  hotel_count: 247,
  question_share: 0,
  label_groups: {
    weak_website: {
      evaluated: 15,
      accuracy: 0.8,
      label_count: 40,
      characteristics: {},
    },
  },
  coverage: { known_pairs: 10, total_pairs: 20, ratio: 0.5 },
  provenance: {
    known: 20,
    synthetic: 3,
    non_synthetic: 17,
    mixed: 0,
    unknown_stale_disputed: 7,
    synthetic_share: 0.15,
  },
  status: "measured",
  limitations: [],
  generated_at: at,
};
const browser = await chromium.launch({
  headless: true,
  executablePath:
    process.env.OKNO_BROWSER_EXECUTABLE ||
    (existsSync(chrome) ? chrome : undefined),
});
async function capture(page, name, locator) {
  const fit = await page.evaluate(() => ({
    width: document.documentElement.scrollWidth,
    viewport: innerWidth,
    elements: [...document.querySelectorAll("body *")]
      .filter((node) => node.getBoundingClientRect().right > innerWidth + 1)
      .slice(0, 8)
      .map((node) => `${node.tagName}.${node.className}`),
  }));
  check(
    fit.width <= fit.viewport + 1,
    `${name}: content fits viewport: ${JSON.stringify(fit)}`,
  );
  const path = resolve(artifacts, `${name}.png`);
  if (locator) await locator.screenshot({ path });
  else await page.screenshot({ path });
  report.captures.push(path);
}
try {
  for (const width of process.env.OKNO_HOTELIER_WIDTH
    ? [Number(process.env.OKNO_HOTELIER_WIDTH)]
    : [1440, 390])
    for (const theme of ["light", "dark"]) {
      const name = `${width}-${theme}`,
        errors = [];
      let questions = specs.map((s, i) => ({
        id: `question-${i}`,
        hotel_id: hotel.id,
        characteristic_ids: [s.id],
        text: [
          "Какие питомцы разрешены, до какого веса и какая доплата за ночь?",
          "Есть ли заявленная шумоизоляция и какие окна установлены?",
          "На каких сторонах заметно вечернее солнце?",
        ][i],
        hypothesis: null,
        options: ["Да", "Нет", "Частично", "Не знаю"],
        requested_scopes: [],
        reason: "unknown",
        status: "pending",
        priority: 1,
        demand_estimate: [20, 3, 100][i],
        demand_synthetic: i === 2,
      }));
      const answers = new Map(),
        history = [];
      let serial = 0;
      const context = await browser.newContext({
        viewport: { width, height: 1000 },
        colorScheme: theme,
        locale: "ru-RU",
      });
      await context.addInitScript(
        ({ origin, theme }) => {
          if (location.origin !== origin) return;
          localStorage.setItem("okno-theme", theme);
          sessionStorage.setItem("okno-token", "synthetic-browser-session");
          sessionStorage.setItem(
            "okno-hotelier-session",
            JSON.stringify({ role: "demo_admin", hotel_ids: [] }),
          );
        },
        { origin: new URL(base).origin, theme },
      );
      context.on("page", (page) =>
        page.on("pageerror", (error) => errors.push(error.message)),
      );
      await context.route("**/*", async (route) => {
        const request = route.request(),
          url = new URL(request.url()),
          path = url.pathname;
        if (
          /(?:^|\.)(?:yandex\.[a-z.]+|ya\.ru|mapillary\.com)$/.test(
            url.hostname,
          )
        )
          return route.abort();
        if (path.startsWith("/api/")) {
          if (!["GET", "HEAD", "OPTIONS"].includes(request.method()))
            report.fixture_posts.push(path);
          let body;
          if (path === "/api/hotels") body = hotels;
          else if (path === "/api/characteristics") body = specs;
          else if (path === "/api/status")
            body = {
              hotel_count: 247,
              characteristic_count: 3,
              mode: "offline",
              region_name: "Сочи",
            };
          else if (path === "/api/revision")
            body = { instance: "fixture", revision: 1 };
          else if (path === "/api/hotelier")
            body = hotels.map((h) => ({
              hotel_id: h.id,
              pending_count:
                h.id === "brand-name-hotel"
                  ? 10
                  : h.id === hotel.id
                    ? questions.filter((q) => q.status === "pending").length
                    : 0,
            }));
          else if (path === `/api/hotelier/${hotel.id}`)
            body = {
              hotel,
              facts: specs.map((s) => fact(s.id, null)),
              claims: [],
              questions: questions.filter((q) => q.status === "pending"),
              answered_questions: questions.filter(
                (q) => q.status === "answered",
              ),
              answers: history,
              conflicts: [],
              passports: [],
              strengths: [],
              demand_synthetic: false,
            };
          else if (path === "/api/questions") {
            const cid = request.postDataJSON().characteristic_id;
            body = questions.find(
              (q) =>
                q.status === "pending" && q.characteristic_ids.includes(cid),
            );
            if (!body) {
              body = {
                ...questions.find((q) => q.characteristic_ids.includes(cid)),
                id: `new-${serial++}`,
                status: "pending",
              };
              questions.push(body);
            }
          } else if (/\/questions\/.+\/(?:answer|simulate)$/.test(path)) {
            const q = questions.find((q) => q.id === path.split("/")[3]);
            const synthetic = path.endsWith("/simulate");
            const raw = synthetic
              ? "Принимаем собак до 5 кг за 500 рублей за ночь."
              : request.postDataJSON().text;
            const unknown = raw === "Не знаю";
            const value =
              q.characteristic_ids[0] === "soundproofing"
                ? { available: false }
                : {
                    available: synthetic ? true : null,
                    species: ["dog"],
                    excluded_species: ["cat"],
                    max_weight_kg: 5,
                    fee_rub_night: 500,
                  };
            body = {
              id: `answer-${serial++}`,
              question_id: q.id,
              raw_text: raw,
              updates: unknown
                ? []
                : [
                    {
                      characteristic_id: q.characteristic_ids[0],
                      value,
                      scope,
                      confidence: 0.85,
                      quote: raw,
                    },
                  ],
              unknown_fields: unknown ? q.characteristic_ids : [],
              disposition: unknown ? "unknown" : "parsed",
              needs_clarification: null,
              confirmed: false,
              synthetic,
              created_claim_ids: [],
              created_at: at,
            };
            answers.set(body.id, body);
          } else if (/\/answers\/.+\/confirm$/.test(path)) {
            body = { ...answers.get(path.split("/")[3]), confirmed: true };
            if (!history.some((a) => a.id === body.id)) history.push(body);
            questions = questions.map((q) =>
              q.id === body.question_id ? { ...q, status: "answered" } : q,
            );
          } else if (path === "/api/hotelier/logout")
            body = { logged_out: true, synthetic: true };
          else if (path === "/api/runs") body = [collection, publicRun];
          else if (path === "/api/runs/collection") body = collection;
          else if (path === "/api/runs/public-search") body = publicRun;
          else if (path === "/api/quality") body = quality;
          else if (path === "/api/changes") body = [change];
          else if (path === "/api/characteristics/compile")
            body = compileResult;
          else if (path === "/api/characteristics/dry-run")
            body = {
              spec_id: compiledSpec.id,
              run_id: "dry-fixture",
              valid: true,
              total: 247,
              processed: 3,
              matched_count: 1,
              results: hotels.slice(0, 3).map((h, i) => ({
                hotel_id: h.id,
                hotel_name: h.name,
                matched: i === 0,
                claims: [],
                warnings: [],
              })),
            };
          else if (path.endsWith("/geo"))
            body = {
              hotel_id: hotel.id,
              center: [hotel.lon, hotel.lat],
              facades: [],
            };
          else {
            errors.push(`Unexpected API call: ${request.method()} ${path}`);
            return route.fulfill({
              status: 409,
              json: { detail: "Fixture blocks unexpected calls" },
            });
          }
          return route.fulfill({ json: body });
        }
        if (path.startsWith("/data/")) {
          if (path.endsWith("hotel_photos.json"))
            return route.fulfill({ json: { photos: {} } });
          if (path.endsWith("building_snapshots.json"))
            return route.fulfill({ json: { models: {} } });
          if (path.endsWith(".geojson"))
            return route.fulfill({
              json: { type: "FeatureCollection", features: [] },
            });
          return route.abort();
        }
        return route.continue();
      });
      const page = await context.newPage();
      await page.goto(`${base}/hotelier`);
      await page.getByTestId("hotelier-question-queue").waitFor();
      check(
        (await page.getByTestId("hotelier-hotel").inputValue()) === hotel.id,
        `${name}: defaults to a non-brand hotel with questions`,
      );
      check(
        (await page.locator("body").innerText()).includes(
          "Демо-кабинет: можно переключаться между 247",
        ),
        `${name}: catalogue switcher is clearly demo`,
      );
      check(
        (await page.getByTestId("hotelier-remaining").innerText()) ===
          "Осталось вопросов: 3",
        `${name}: remaining question count`,
      );
      const order = await page
        .getByTestId("hotelier-question-queue")
        .locator("button")
        .allTextContents();
      check(
        order[0].includes("20 раз искали") &&
          order[1].includes("3 раза искали") &&
          order[2].includes("Спрос · демо"),
        `${name}: real demand before synthetic with explicit provenance`,
      );
      check(
        !/\{'|\b(?:pets|soundproofing|evening_heat)\b/u.test(
          await page.getByTestId("hotelier-question").innerText(),
        ),
        `${name}: question has no raw dictionary or characteristic code`,
      );
      await page.getByTestId("unknown-question-evening_heat").click();
      check(
        (await page
          .getByTestId("queue-question-evening_heat")
          .getAttribute("aria-pressed")) === "true",
        `${name}: unknown tile selects matching question`,
      );
      check(
        (
          await page.getByTestId("queue-question-evening_heat").innerText()
        ).includes("Вечернее солнце"),
        `${name}: catalogue display name is shared`,
      );
      await page.getByTestId("queue-question-soundproofing").click();
      await page.getByRole("button", { name: "Нет", exact: true }).click();
      await page.getByTestId("answer-preview").click();
      await page.getByTestId("answer-fields").waitFor();
      check(
        (await page.getByTestId("answer-fields").innerText()).includes(
          "Шумоизоляции нет",
        ),
        `${name}: No has a human negative preview`,
      );
      await page.getByTestId("queue-question-pets").click();
      await page.getByTestId("answer-simulate").click();
      await page.getByTestId("answer-fields").waitFor();
      check(
        (await page.getByTestId("hotelier-answer").inputValue()).includes(
          "Принимаем собак",
        ),
        `${name}: simulator raw text is editable`,
      );
      await page
        .getByTestId("hotelier-answer")
        .fill(
          "Собак до 5 кг принимаем, кошек нет. Доплата 500 руб в сутки, в июле и августе с животными не заселяем.",
        );
      await page.getByTestId("answer-preview").click();
      await page.getByTestId("answer-fields").waitFor();
      const preview = await page.getByTestId("answer-fields").innerText();
      check(
        preview.includes("Доплата 500 ₽ за сутки") &&
          !preview.includes("Ответ отеля · демо") &&
          !preview.includes('"available"'),
        `${name}: manual partial preview is human and not labelled simulator`,
      );
      await capture(
        page,
        `hotelier-preview-${name}`,
        page.getByTestId("answer-fields"),
      );
      await page.getByRole("button", { name: "Не знаю", exact: true }).click();
      await page.getByTestId("answer-preview").click();
      await page.getByTestId("answer-confirm").click();
      await page
        .getByText("Спасибо. Ответ «Не знаю» принят; оставим «нет данных».", {
          exact: true,
        })
        .waitFor();
      await page
        .getByTestId("hotelier-remaining")
        .filter({ hasText: "Осталось вопросов: 2" })
        .waitFor();
      check(
        (await page.getByTestId("hotelier-remaining").innerText()) ===
          "Осталось вопросов: 2",
        `${name}: unknown closes a question`,
      );
      await page.getByRole("heading", { name: "Ваши ответы" }).waitFor();
      await capture(
        page,
        `hotelier-queue-${name}`,
        page.locator(".question-stack"),
      );
      await page.goto(`${base}/agent`);
      await page.getByTestId("agent-summary").waitFor();
      check(
        (await page.getByTestId("agent-summary").innerText()).includes(
          "завершено 2",
        ),
        `${name}: agent summary uses loaded run counts`,
      );
      check(
        !/SSE|TTL|claims are/u.test(await page.locator("body").innerText()),
        `${name}: agent technical rationale stays in developer details`,
      );
      await page.getByRole("button", { name: /Поиск.*Запрос гостя/u }).click();
      await page.getByTestId("agent-step-0").waitFor();
      check(
        !(await page.locator("body").textContent()).includes(
          "PRIVATE GUEST QUERY",
        ),
        `${name}: public search exposes no private query text`,
      );
      await capture(page, `agent-${name}`);
      await page.goto(`${base}/quality`);
      await page.getByTestId("quality-summary").waitFor();
      const summary = await page.getByTestId("quality-summary").innerText();
      check(
        summary.includes("Вручную проверено: 0") &&
          summary.includes("12 из 15") &&
          summary.includes("значений: 7"),
        `${name}: quality summary uses real DTO fields`,
      );
      check(
        !/Brier|holdout|nDCG|ECE/u.test(await page.locator("body").innerText()),
        `${name}: advanced quality terms stay closed`,
      );
      await capture(page, `quality-${name}`);
      await page.goto(`${base}/changes`);
      await page.getByTestId("changes-summary").waitFor();
      const changed = await page.getByTestId("changes-summary").innerText();
      check(
        changed.includes("отелей: 1") &&
          changed.includes("уведомлений: 1") &&
          changed.includes("Вечернее солнце") &&
          !changed.includes("С питомцем"),
        `${name}: changes summary preserves hotel, characteristic and notification counts`,
      );
      check(
        !/OSM_BUILDING_NEW|TTL/u.test(await page.locator("body").innerText()),
        `${name}: event and expiry codes are not primary UI`,
      );
      await capture(page, `changes-${name}`);
      await page.goto(`${base}/studio`);
      await page
        .getByTestId("studio-description")
        .fill("Кафе в пяти минутах пешком");
      await page.getByTestId("studio-compile").click();
      await page.getByTestId("studio-distance-warning").waitFor();
      await page.getByTestId("studio-dry-run").click();
      await page.getByTestId("studio-sample-summary").waitFor();
      const sample = await page
        .getByTestId("studio-sample-summary")
        .innerText();
      check(
        sample.includes("В выборке подходит 1 из 3") &&
          sample.includes("Проверено 3 из 247"),
        `${name}: Studio separates sample matches from catalogue size`,
      );
      check(
        (await page.locator(".dry-hotel").first().innerText()).includes(
          "Островок",
        ),
        `${name}: dry run shows a hotel name`,
      );
      check(
        await page.getByTestId("studio-distance-warning").isVisible(),
        `${name}: minutes to straight-line metres warning stays visible before publishing`,
      );
      await capture(page, `studio-${name}`, page.locator(".dry-results"));
      await page.goto(`${base}/hotelier`);
      await page.getByTestId("hotelier-question-queue").waitFor();
      await page.evaluate(() => {
        sessionStorage.removeItem("okno-token");
        sessionStorage.removeItem("okno-hotelier-session");
        window.dispatchEvent(new Event("okno-auth-changed"));
      });
      await page.getByTestId("hotelier-login").waitFor();
      await capture(page, `login-${name}`);
      check(
        (await page.getByTestId("hotelier-answer").count()) === 0,
        `${name}: global logout immediately clears private cabinet`,
      );
      check(
        errors.length === 0,
        `${name}: no browser exceptions or unexpected API calls: ${JSON.stringify(errors)}`,
      );
      const overflow = await page.evaluate(() => ({
        width: document.documentElement.scrollWidth,
        viewport: innerWidth,
        elements: [...document.querySelectorAll("body *")]
          .filter((node) => node.getBoundingClientRect().right > innerWidth + 1)
          .slice(0, 10)
          .map((node) => `${node.tagName}.${node.className}`),
      }));
      check(
        overflow.width <= overflow.viewport + 1,
        `${name}: no horizontal overflow: ${JSON.stringify(overflow)}`,
      );
      console.log(`Verified ${name}`);
      await page.close();
      await context.close();
      console.log(`Closed ${name}`);
    }
  report.status = "pass";
} catch (error) {
  report.status = "fail";
  report.error = error.stack;
  throw error;
} finally {
  await browser.close();
  await writeFile(
    resolve(artifacts, "report.json"),
    JSON.stringify(report, null, 2),
  );
}
console.log(
  `PASS: ${report.checks.length} fixture checks, ${report.captures.length} captures, no shared database writes; ${artifacts}`,
);
