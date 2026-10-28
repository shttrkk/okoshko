import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { isDeepStrictEqual } from "node:util";
import { chromium } from "playwright";

const root = fileURLToPath(new URL("../..", import.meta.url));
const chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const texts = {
  rooms:
    "Номера 201–210 и 301–310 смотрят на море, у всех балкон. 211–220 во двор, без балкона. На первом этаже номеров нет.",
  pets: "Собак до 5 кг принимаем, кошек нет. Доплата 500 руб в сутки, в июле и августе с животными не заселяем.",
  correction:
    "Номера 201–212 смотрят на море, у всех балкон. Номера 213–220 во двор, без балкона. На первом этаже номеров нет.",
  route:
    "Да. До пляжа по набережной без ступенек, с коляской проходят свободно, у входа в отель пандус.",
  wifi: "Wi-Fi во всех номерах бесплатный. Скорость не измеряли.",
};
const ids = ["room_orientation", "pets", "accessible_route", "connectivity"];
const pending = (cabinet) =>
  cabinet.questions.filter((q) => q.status === "pending");
const allQuestions = (cabinet) => [
  ...cabinet.questions,
  ...cabinet.answered_questions,
];
const subset = (value, keys) =>
  Object.fromEntries(
    keys
      .filter((key) => value[key] !== undefined)
      .map((key) => [key, value[key]]),
  );
const compact = (cabinet) => ({
  hotel: subset(cabinet.hotel, ["id", "name"]),
  pending_count: pending(cabinet).length,
  questions: allQuestions(cabinet).map((q) =>
    subset(q, [
      "id",
      "characteristic_ids",
      "status",
      "reason",
      "hypothesis",
      "hypothesis_update",
    ]),
  ),
  answers: cabinet.answers
    .filter((a) => a.updates.some((u) => ids.includes(u.characteristic_id)))
    .map((a) =>
      subset(a, [
        "id",
        "question_id",
        "raw_text",
        "confirmed",
        "synthetic",
        "created_claim_ids",
        "updates",
      ]),
    ),
  claims: cabinet.claims
    .filter((c) => ids.includes(c.characteristic_id))
    .map((c) =>
      subset(c, [
        "id",
        "characteristic_id",
        "scope",
        "value",
        "source",
        "method",
        "synthetic",
        "supersedes",
        "supersedes_ids",
      ]),
    ),
  facts: cabinet.facts
    .filter((f) => ids.includes(f.characteristic_id))
    .map((f) =>
      subset(f, [
        "id",
        "characteristic_id",
        "scope",
        "value",
        "status",
        "confidence",
        "synthetic",
        "supporting_claim_ids",
      ]),
    ),
});
function safeBase(value) {
  assert.ok(
    value,
    "Set OKNO_BASE_URL explicitly; writes have no default destination",
  );
  const url = new URL(value);
  assert.ok(
    ["http:", "https:"].includes(url.protocol) &&
      ["localhost", "127.0.0.1"].includes(url.hostname) &&
      url.port === "8081" &&
      !url.username &&
      !url.password,
    "R3 writes are restricted to localhost:8081 or 127.0.0.1:8081; port 8080 is forbidden",
  );
  return new URL(url.origin);
}
function provider(host) {
  return /(^|\.)(?:yandex\.[a-z.]+|ya\.ru|yastatic\.net|mapillary\.[a-z.]+)$/.test(
    host.toLowerCase(),
  );
}
function writable(path) {
  return (
    [
      "/api/search",
      "/api/compare",
      "/api/questions",
      "/api/hotelier/login",
      "/api/hotelier/logout",
    ].includes(path) ||
    /^\/api\/hotelier\/[^/]+\/questions\/[^/]+$/.test(path) ||
    /^\/api\/questions\/[^/]+\/answer$/.test(path) ||
    /^\/api\/answers\/[^/]+\/(?:revise|confirm)$/.test(path) ||
    /^\/api\/hotels\/[^/]+\/evaluate$/.test(path)
  );
}
async function until(read, valid, label, timeout = 60000) {
  const end = Date.now() + timeout;
  let value;
  while (Date.now() < end) {
    value = await read();
    if (valid(value)) return value;
    await new Promise((done) => setTimeout(done, 150));
  }
  throw new Error(
    `${label}: timed out; last value=${JSON.stringify(value).slice(0, 400)}`,
  );
}
function query(cid) {
  const wifi = cid === "connectivity";
  return {
    text:
      cid === "pets"
        ? "с собакой 4 кг"
        : wifi
          ? "Wi-Fi не меньше 30 Мбит/с"
          : "Доступный маршрут до пляжа для коляски",
    month: 9,
    must: [
      {
        characteristic_id: cid,
        label: wifi
          ? "Связь для работы ≥ 30 Мбит/с"
          : cid === "pets"
            ? "С питомцем"
            : "Доступность маршрута до пляжа для коляски",
        field: wifi ? "download_mbps" : "available",
        op: wifi ? "gte" : "eq",
        value: wifi ? 30 : true,
      },
    ],
    nice: [],
    rooms: [],
    no_data: [],
    no_data_must: [],
    unmapped: [],
    clarifications: [],
    mode: "offline",
    ...(cid === "pets" ? { pet_weight_kg: 4 } : {}),
  };
}

// Writes are deliberately opt-in even when this module is imported by panel-live.
// No response fixtures, storageState, HAR, trace recording, SQL or seed changes.
export async function runPanelR3Actions({ base, artifacts } = {}) {
  const stand = safeBase(base || process.env.OKNO_BASE_URL);
  assert.equal(
    process.env.OKNO_PANEL_R3_WRITES,
    "1",
    "Set OKNO_PANEL_R3_WRITES=1 to enable the isolated R3 action checks",
  );
  const output = resolve(
    artifacts instanceof URL
      ? fileURLToPath(artifacts)
      : artifacts || `${root}/runtime/panel-r3-actions`,
  );
  await mkdir(output, { recursive: true });
  const reportPath = resolve(output, "panel-r3-actions-report.json");
  const report = {
    status: "running",
    test_copy: true,
    real_api: true,
    stand_origin: stand.origin,
    started_at: new Date().toISOString(),
    checks: [],
    captures: [],
    cases: {},
    case_steps: {},
    requests: [],
    blocked_providers: [],
    blocked_navigation: [],
    forbidden_mutations: [],
    browser_errors: [],
    authentication:
      "Token stays in a JavaScript closure and an ephemeral browser session; no token/header/storageState/trace is saved",
    limitations: [
      "These are operator test replies on an isolated copy, not verified statements from real hotels. Seed files are not edited.",
      "R3/R4 guest criteria are explicit API setup; parser intent is checked by the separate R11 workflow.",
    ],
  };
  let token = "",
    browser,
    currentPage,
    phase = "setup";
  const contexts = new Set();
  const clean = (value) =>
    String(value)
      .split(token || "[unused-secret]")
      .join("[token redacted]")
      .replace(/Bearer\s+\S+/g, "Bearer [redacted]");
  const check = (ok, label) => {
    assert.ok(ok, label);
    report.checks.push({ phase, label });
  };
  function guard(method, url) {
    if (["GET", "HEAD", "OPTIONS"].includes(method)) return true;
    // Repeat the origin/port check for every browser and Node mutation.
    const allowed =
      url.origin === stand.origin &&
      ["localhost", "127.0.0.1"].includes(url.hostname) &&
      url.port === "8081" &&
      method === "POST" &&
      writable(url.pathname);
    if (!allowed)
      report.forbidden_mutations.push({
        method,
        origin: url.origin,
        path: url.pathname,
      });
    return allowed;
  }
  async function api(path, body, auth = false) {
    const url = new URL(path, stand),
      method = body === undefined ? "GET" : "POST";
    check(
      url.origin === stand.origin && !provider(url.hostname),
      "API remains on the isolated origin",
    );
    check(
      guard(method, url),
      `Allowed API operation: ${method} ${url.pathname}`,
    );
    const response = await fetch(url, {
      method,
      redirect: "manual",
      signal: AbortSignal.timeout(60000),
      headers: {
        Accept: "application/json",
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
        ...(auth ? { Authorization: `Bearer ${token}` } : {}),
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    report.requests.push({
      method,
      path: url.pathname,
      status: response.status,
      via: "api",
    });
    const raw = await response.text();
    let data;
    try {
      data = JSON.parse(raw);
    } catch {
      throw new Error(`${method} ${url.pathname}: non-JSON ${response.status}`);
    }
    assert.ok(
      response.ok,
      clean(
        `${method} ${url.pathname}: ${response.status} ${JSON.stringify(data).slice(0, 600)}`,
      ),
    );
    return data;
  }
  async function surface({ authenticated = false, parsed = null } = {}) {
    const context = await browser.newContext({
      viewport: { width: 1440, height: 1000 },
      locale: "ru-RU",
      colorScheme: "light",
      serviceWorkers: "block",
    });
    contexts.add(context);
    // Installed before newPage/navigation; popups share this provider guard.
    await context.route("**/*", async (route) => {
      const request = route.request(),
        url = new URL(request.url());
      if (provider(url.hostname)) {
        report.blocked_providers.push({ host: url.hostname });
        return route.abort("blockedbyclient");
      }
      if (request.isNavigationRequest() && url.origin !== stand.origin) {
        report.blocked_navigation.push({ host: url.hostname });
        return route.abort("blockedbyclient");
      }
      if (!guard(request.method(), url)) return route.abort("blockedbyclient");
      return route.continue();
    });
    await context.addInitScript(
      ({ origin, auth, bearer, parsed }) => {
        if (location.origin !== origin) return;
        if (auth) {
          sessionStorage.setItem("okno-token", bearer);
          sessionStorage.setItem(
            "okno-hotelier-session",
            JSON.stringify({ role: "demo_admin", hotel_ids: [] }),
          );
        } else {
          sessionStorage.removeItem("okno-token");
          sessionStorage.removeItem("okno-hotelier-session");
        }
        if (parsed)
          sessionStorage.setItem("okno-parsed", JSON.stringify(parsed));
        else sessionStorage.removeItem("okno-parsed");
      },
      {
        origin: stand.origin,
        auth: authenticated,
        bearer: authenticated ? token : "",
        parsed,
      },
    );
    context.on("page", (page) =>
      page.on("pageerror", (error) =>
        report.browser_errors.push(clean(error.message)),
      ),
    );
    const page = await context.newPage();
    page.setDefaultTimeout(60000);
    return page;
  }
  async function capture(page, name, locator) {
    const file = resolve(output, `${name}.png`);
    if (locator) await locator.screenshot({ path: file });
    else await page.screenshot({ path: file });
    report.captures.push({ phase, name, path: file });
  }
  const cabinet = (hotelId) =>
    api(`/api/hotelier/${encodeURIComponent(hotelId)}`, undefined, true);
  async function ensureEditable(hotelId, cid) {
    const data = await cabinet(hotelId);
    const open = pending(data).find((q) => q.characteristic_ids.includes(cid));
    if (open) return open;
    const old = [...data.answers]
      .reverse()
      .find(
        (a) =>
          a.confirmed && a.updates.some((u) => u.characteristic_id === cid),
      );
    return old
      ? api(`/api/answers/${encodeURIComponent(old.id)}/revise`, {}, true)
      : api(
          `/api/hotelier/${encodeURIComponent(hotelId)}/questions/${cid}`,
          {},
          true,
        );
  }
  async function select(page, hotelId, q) {
    await page.goto(new URL("/hotelier", stand).href);
    await page.getByTestId("hotelier-hotel").waitFor();
    await page.getByTestId("hotelier-hotel").selectOption(hotelId);
    const option = page.locator(
      `[data-testid="hotelier-question-select"] option[value="${q.id}"]`,
    );
    await until(
      () => option.count(),
      (n) => n > 0 || false,
      `Question ${q.id} is in the actual queue`,
    );
    await page.getByTestId("hotelier-question-select").selectOption(q.id);
    check(
      (await page.getByTestId("hotelier-question-select").inputValue()) ===
        q.id,
      `UI selected ${q.characteristic_ids.join(",")} on the returned question id`,
    );
  }
  async function responseJson(response, label) {
    report.requests.push({
      method: response.request().method(),
      path: new URL(response.url()).pathname,
      status: response.status(),
      via: "operator_ui",
    });
    const value = await response.json();
    check(
      response.ok(),
      clean(
        `${label}: HTTP ${response.status()} ${response.ok() ? "" : JSON.stringify(value)}`,
      ),
    );
    return value;
  }
  async function operatorConfirm(page, hotelId, q, text, name) {
    await page.getByTestId("hotelier-answer").fill(text);
    const previewResponse = page.waitForResponse(
      (r) =>
        r.request().method() === "POST" &&
        new URL(r.url()).pathname === `/api/questions/${q.id}/answer`,
    );
    await page.getByTestId("answer-preview").click();
    const preview = await responseJson(
      await previewResponse,
      `${name} preview`,
    );
    check(
      preview.question_id === q.id &&
        preview.updates.length > 0 &&
        preview.disposition === "parsed",
      `${name}: preview has the exact original question and factual updates`,
    );
    check(
      preview.updates.every((u) =>
        q.characteristic_ids.includes(u.characteristic_id),
      ),
      `${name}: preview answers the expected characteristic`,
    );
    check(
      preview.synthetic === false,
      `${name}: manual test reply is not mislabelled as a simulator reply`,
    );
    check(
      preview.updates.every((u) => text.includes(u.quote)),
      `${name}: every extracted quote is verbatim`,
    );
    await page.getByTestId("answer-fields").waitFor();
    const beforeCounter = await page
      .getByTestId("hotelier-remaining")
      .innerText();
    report.case_steps[name] = {
      question_id: q.id,
      characteristic_ids: q.characteristic_ids,
      preview,
      before_counter: beforeCounter,
    };
    await capture(
      page,
      `${name}-before-confirm`,
      page.locator(".question-stack"),
    );
    const marker = `${name}-${Date.now()}`;
    await page.evaluate((marker) => {
      window.__panelR3ConfirmMarker = marker;
    }, marker);
    const confirmResponse = page.waitForResponse(
      (r) =>
        r.request().method() === "POST" &&
        new URL(r.url()).pathname === `/api/answers/${preview.id}/confirm`,
    );
    await page.getByTestId("answer-confirm").click();
    const accepted = await responseJson(
      await confirmResponse,
      `${name} confirm`,
    );
    check(
      accepted.confirmed &&
        accepted.question_id === q.id &&
        accepted.created_claim_ids.length > 0,
      `${name}: confirmation actually created claims for the same question`,
    );
    const after = await cabinet(hotelId);
    Object.assign(report.case_steps[name], { accepted, after: compact(after) });
    check(
      !pending(after).some((item) => item.id === q.id) &&
        after.answered_questions.some((item) => item.id === q.id),
      `${name}: exactly the answered question is closed`,
    );
    const created = after.claims.filter((claim) =>
      accepted.created_claim_ids.includes(claim.id),
    );
    check(
      created.length === accepted.created_claim_ids.length &&
        created.every(
          (claim) =>
            claim.hotel_id === hotelId &&
            q.characteristic_ids.includes(claim.characteristic_id) &&
            !claim.synthetic &&
            claim.source === "hotelier",
        ),
      `${name}: every created claim belongs to the intended hotel/CID and real manual source`,
    );
    for (const update of preview.updates) {
      check(
        typeof update.quote === "string" &&
          update.quote.trim().length > 0 &&
          preview.raw_text.includes(update.quote),
        `${name}: extracted evidence is a nonempty verbatim substring of the full answer`,
      );
      const claim = created.find(
        (claim) =>
          claim.characteristic_id === update.characteristic_id &&
          isDeepStrictEqual(claim.scope, update.scope) &&
          isDeepStrictEqual(claim.value, update.value),
      );
      check(
        !!claim &&
          claim.evidence.some(
            (evidence) =>
              evidence.quote === update.quote &&
              evidence.message_id === accepted.id &&
              evidence.output.characteristic_id === update.characteristic_id &&
              isDeepStrictEqual(evidence.output.value, update.value),
          ),
        `${name}: the exact extracted update quote belongs to its created claim/evidence`,
      );
    }
    await page.getByTestId(`revise-answer-${accepted.id}`).waitFor();
    await until(
      () => page.getByTestId("hotelier-remaining").innerText(),
      (s) => s === `Осталось вопросов: ${pending(after).length}`,
      `${name}: queue counter updates without a reload`,
    );
    check(
      (await page.evaluate(() => window.__panelR3ConfirmMarker)) === marker,
      `${name}: confirmation/history/queue update occurred without page reload`,
    );
    await until(
      () =>
        page
          .locator(
            `[data-testid="hotelier-question-select"] option[value="${q.id}"]`,
          )
          .count(),
      (count) => count === 0,
      `${name}: the closed question disappears from the UI queue without reload`,
    );
    const topic = after.facts.filter(
      (f) =>
        q.characteristic_ids.includes(f.characteristic_id) &&
        f.supporting_claim_ids.some((id) =>
          accepted.created_claim_ids.includes(id),
        ),
    );
    check(
      topic.length > 0 &&
        topic.every(
          (f) => f.status === "confirmed" && f.confidence > 0 && !f.synthetic,
        ),
      `${name}: supporting facts are real-hotelier confirmed, not unknown/0 or synthetic`,
    );
    await capture(
      page,
      `${name}-after-confirm`,
      page.locator(".question-stack"),
    );
    await capture(
      page,
      `${name}-history`,
      page.getByTestId(`revise-answer-${accepted.id}`).locator(".."),
    );
    return {
      preview,
      accepted,
      after,
      beforeCounter,
      afterCounter: await page.getByTestId("hotelier-remaining").innerText(),
      facts: topic,
    };
  }
  async function guestFact(
    hotelId,
    cid,
    name,
    { parsed = null, quote = null, pattern } = {},
  ) {
    const page = await surface({ parsed });
    currentPage = page;
    await page.goto(
      new URL(`/hotel/${encodeURIComponent(hotelId)}`, stand).href,
    );
    const card = page.getByTestId(`fact-card-${cid}`);
    await card.waitFor();
    // A requested criterion is primary. With no query, all fact categories are rendered.
    const text = await card.innerText();
    if (pattern)
      check(
        pattern.test(text),
        `${name}: guest sees the substantive human fact`,
      );
    if (quote)
      check(
        (await card.getByTestId(`evidence-${cid}`).innerText()).includes(
          "Подтвердил отель",
        ),
        `${name}: real guest fact has the hotel-confirmation badge`,
      );
    await card.getByTestId(`evidence-${cid}`).click();
    const dialog = page.getByRole("dialog");
    await dialog.waitFor();
    if (quote)
      await until(
        () => dialog.innerText(),
        (text) => text.includes(quote),
        `${name}: guest evidence retains the exact operator quote`,
      );
    await capture(page, `${name}-guest-evidence`, dialog);
    await page.keyboard.press("Escape");
    await capture(page, `${name}-guest`, card);
    return { page, card, text };
  }
  try {
    const baselinePath =
      process.env.OKNO_PANEL_R3_BASELINE ||
      resolve(root, "docs/reviews/panel_r3/baseline_data.json");
    let baseline = null;
    if (existsSync(baselinePath)) {
      baseline = JSON.parse(await readFile(baselinePath, "utf8"));
      report.baseline_file = baselinePath;
    }
    const roomText =
      baseline?.answers?.room_orientation?.updates?.[0]?.quote || texts.rooms;
    const petText = baseline?.answers?.pets?.updates?.[0]?.quote || texts.pets;
    const catalogue = await api("/api/characteristics");
    check(
      catalogue.find((spec) => spec.id === "connectivity")
        ?.hotelier_answerable === true &&
        catalogue
          .find((spec) => spec.id === "room_orientation")
          ?.value_schema?.[
            "x-hotelier"
          ]?.substantive_fields?.includes("groups"),
      "The rebuilt R3 registry is present before any login/question/answer POST",
    );
    const login = await api("/api/hotelier/login", {
      username: process.env.OKNO_HOTELIER_USERNAME || "hotelier",
      password: process.env.OKNO_HOTELIER_PASSWORD || "hotelier123",
    });
    token = login.token;
    check(
      typeof token === "string" &&
        token.length > 0 &&
        login.role === "demo_admin",
      "Isolated API setup has an in-memory demo-admin session",
    );
    const hotels = await api("/api/hotels");
    const target =
      hotels.find((h) => /swiss[oô]tel|камелия/i.test(h.name)) ||
      hotels.find((h) => !h.tags?.catalogue_review);
    check(
      !!target,
      "A catalogue hotel is available without creating a seed object",
    );
    report.target = subset(target, ["id", "name"]);
    browser = await chromium.launch({
      headless: true,
      executablePath:
        process.env.OKNO_BROWSER_EXECUTABLE ||
        (existsSync(chrome) ? chrome : undefined),
    });
    const operator = await surface({ authenticated: true });
    currentPage = operator;
    // Seed-independent setup: preserve another pending characteristic for R2.
    const other = await api(
      `/api/hotelier/${encodeURIComponent(target.id)}/questions/soundproofing`,
      {},
      true,
    );
    report.control_pending_question = {
      id: other.id,
      characteristic_ids: other.characteristic_ids,
    };
    phase = "R1-room-orientation";
    const before = await cabinet(target.id);
    report.cases.legacy = { present: false };
    if (baseline) {
      const oldIds = Object.values(baseline.answers || {}).map(
        (a) => a.answer_id,
      );
      const oldAnswers = before.answers.filter((a) => oldIds.includes(a.id));
      if (oldAnswers.length) {
        check(
          oldAnswers.every((answer) => answer.created_claim_ids.length > 0),
          "Historical parsed test replies have durable created-claim ids",
        );
        const oldClaims = oldAnswers.flatMap((a) => a.created_claim_ids);
        const retained = oldClaims.map((id) =>
          before.claims.find((claim) => claim.id === id),
        );
        check(
          retained.every(
            (claim) =>
              claim &&
              claim.source === "hotelier" &&
              !claim.synthetic &&
              oldAnswers.some(
                (answer) =>
                  answer.created_claim_ids.includes(claim.id) &&
                  answer.updates.some(
                    (update) =>
                      update.characteristic_id === claim.characteristic_id &&
                      isDeepStrictEqual(update.scope, claim.scope) &&
                      isDeepStrictEqual(update.value, claim.value),
                  ),
              ),
          ),
          "Historical test-copy claims remain immutable in the ledger, including after a prior action run",
        );
        const archived = oldClaims.filter((id) =>
          before.claims.some(
            (claim) =>
              claim.id !== id &&
              (claim.supersedes === id || claim.supersedes_ids?.includes(id)),
          ),
        );
        const active = oldClaims.filter((id) => !archived.includes(id));
        for (const id of archived) {
          const original = before.claims.find((claim) => claim.id === id);
          check(
            before.claims.some(
              (claim) =>
                claim.id !== id &&
                claim.hotel_id === target.id &&
                claim.characteristic_id === original.characteristic_id &&
                (claim.supersedes === id || claim.supersedes_ids?.includes(id)),
            ),
            "An archived legacy claim has an explicit replacement in the same hotel/characteristic ledger",
          );
        }
        const migrated = before.facts.filter((fact) =>
          fact.supporting_claim_ids.some((id) => active.includes(id)),
        );
        check(
          active.every((id) =>
            migrated.some(
              (fact) =>
                fact.supporting_claim_ids.includes(id) &&
                fact.status === "confirmed" &&
                fact.confidence > 0 &&
                !fact.synthetic,
            ),
          ),
          "Every still-active historical null-available reply materializes as a real confirmed fact after rebuild",
        );
        report.cases.legacy = {
          present: true,
          state: active.length ? "active_confirmed" : "already_superseded",
          active_claim_ids: active,
          archived_claim_ids: archived,
          answer_ids: oldAnswers.map((a) => a.id),
          facts: migrated.map((f) =>
            subset(f, [
              "id",
              "characteristic_id",
              "status",
              "confidence",
              "value",
            ]),
          ),
        };
      }
    }
    const roomQuestion = await ensureEditable(target.id, "room_orientation");
    await select(operator, target.id, roomQuestion);
    const room = await operatorConfirm(
      operator,
      target.id,
      roomQuestion,
      roomText,
      "r1-room",
    );
    check(
      room.facts.some(
        (f) =>
          f.value.available === null &&
          Array.isArray(f.value.groups) &&
          f.value.groups.length === 2 &&
          f.value.absent_floors.includes(1),
      ),
      "R1 preserves room groups and absent floors without inventing hotel-wide availability",
    );
    await guestFact(target.id, "room_orientation", "r1-room", {
      quote: room.preview.updates.find(
        (u) => u.characteristic_id === "room_orientation",
      ).quote,
      pattern: /201[\s\S]*211[\s\S]*во двор/i,
    });
    report.cases.r1_room = {
      before: compact(before),
      after: compact(room.after),
      answer_id: room.accepted.id,
      question_id: roomQuestion.id,
      no_reload: true,
    };
    phase = "R1-partial-pets";
    const petBefore = await cabinet(target.id),
      petQuestion = await ensureEditable(target.id, "pets");
    currentPage = operator;
    await select(operator, target.id, petQuestion);
    const pet = await operatorConfirm(
      operator,
      target.id,
      petQuestion,
      petText,
      "r1-pets",
    );
    check(
      pet.facts.some(
        (f) =>
          f.scope.month === 9 &&
          f.value.available === true &&
          f.value.species.includes("dog") &&
          f.value.max_weight_kg === 5 &&
          f.value.fee_rub_night === 500,
      ),
      "R1 partial pets preserve the accepted dog/weight/nightly fee conditions",
    );
    const excludedMonths = pet.facts.filter((f) =>
      [7, 8].includes(f.scope.month),
    );
    check(
      excludedMonths.length === 2 &&
        excludedMonths.every((f) => f.value.available === false),
      "R1 seasonal exclusions do not become year-round permission",
    );
    await guestFact(target.id, "pets", "r1-pets", {
      parsed: query("pets"),
      quote: pet.preview.updates.find((u) => u.characteristic_id === "pets")
        .quote,
      pattern: /5 кг[\s\S]*500/i,
    });
    report.cases.r1_pets = {
      before: compact(petBefore),
      after: compact(pet.after),
      answer_id: pet.accepted.id,
      question_id: petQuestion.id,
      no_reload: true,
    };
    phase = "R2-same-question-revision";
    currentPage = operator;
    await operator.goto(new URL("/hotelier", stand).href);
    await operator.getByTestId("hotelier-hotel").selectOption(target.id);
    await operator.getByTestId(`revise-answer-${room.accepted.id}`).waitFor();
    const revisionBefore = await cabinet(target.id);
    const questionIdsBefore = (
      await api(`/api/hotels/${encodeURIComponent(target.id)}?compact=true`)
    ).questions
      .map((q) => q.id)
      .sort();
    const oldAnswer = revisionBefore.answers.find(
      (a) => a.id === room.accepted.id,
    );
    const oldClaims = revisionBefore.claims.filter((c) =>
      room.accepted.created_claim_ids.includes(c.id),
    );
    await capture(operator, "r2-before-revision");
    const revisionResponse = operator.waitForResponse(
      (r) =>
        r.request().method() === "POST" &&
        new URL(r.url()).pathname === `/api/answers/${room.accepted.id}/revise`,
    );
    await operator.getByTestId(`revise-answer-${room.accepted.id}`).click();
    const reopened = await responseJson(await revisionResponse, "R2 revise");
    check(
      reopened.id === roomQuestion.id &&
        reopened.hotel_id === target.id &&
        JSON.stringify(reopened.characteristic_ids) ===
          JSON.stringify(["room_orientation"]),
      "R2 returns exactly the original question/CID/hotel",
    );
    await until(
      () => operator.getByTestId("hotelier-question-select").inputValue(),
      (id) => id === reopened.id,
      "R2 form selects the returned original question",
    );
    check(
      (await operator.getByTestId("hotelier-answer").inputValue()) === roomText,
      "R2 original answer is prefilled in the actual correction form",
    );
    const revised = await operatorConfirm(
      operator,
      target.id,
      reopened,
      texts.correction,
      "r2-revision",
    );
    const questionIdsAfter = (
      await api(`/api/hotels/${encodeURIComponent(target.id)}?compact=true`)
    ).questions
      .map((q) => q.id)
      .sort();
    assert.deepEqual(
      questionIdsAfter,
      questionIdsBefore,
      "R2 must not create a false guest question",
    );
    check(
      pending(revised.after).some((q) => q.id === other.id),
      "R2 leaves the unrelated pending soundproofing question open",
    );
    assert.deepEqual(
      revised.after.answers.find((a) => a.id === oldAnswer.id),
      oldAnswer,
      "R2 preserves the original Answer unchanged",
    );
    for (const old of oldClaims)
      assert.deepEqual(
        revised.after.claims.find((c) => c.id === old.id),
        old,
        "R2 preserves immutable historical claims",
      );
    const newClaims = revised.after.claims.filter((c) =>
      revised.accepted.created_claim_ids.includes(c.id),
    );
    check(
      oldClaims.every((c) =>
        newClaims.some(
          (newClaim) =>
            newClaim.supersedes === c.id ||
            newClaim.supersedes_ids?.includes(c.id),
        ),
      ),
      "R2 replacement explicitly supersedes the prior answer claims",
    );
    check(
      revised.facts.some((f) =>
        f.value.groups?.some(
          (g) => g.rooms.includes("212") && g.sea_view === true,
        ),
      ),
      "R2 newly confirmed facts contain the corrected room range",
    );
    report.cases.r2 = {
      before: compact(revisionBefore),
      after: compact(revised.after),
      question_ids_before: questionIdsBefore,
      question_ids_after: questionIdsAfter,
      original_answer_id: oldAnswer.id,
      revised_answer_id: revised.accepted.id,
      question_id: reopened.id,
      no_reload_after_confirm: true,
    };
    phase = "R3-accessible-route";
    const routeQuestion = await ensureEditable(target.id, "accessible_route");
    currentPage = operator;
    await select(operator, target.id, routeQuestion);
    const route = await operatorConfirm(
      operator,
      target.id,
      routeQuestion,
      texts.route,
      "r3-route",
    );
    check(
      route.facts.some(
        (f) =>
          f.value.available === true &&
          f.value.has_steps === false &&
          f.value.wheelchair_suitable === true &&
          f.value.entrance_ramp === true &&
          f.value.distance_m == null,
      ),
      "R3 accepts the exact Igor answer without inventing distance",
    );
    const routeQuery = query("accessible_route"),
      routeDetail = await api(
        `/api/hotels/${encodeURIComponent(target.id)}/evaluate`,
        routeQuery,
      );
    const routeVerdict = routeDetail.verdicts.find(
      (v) => v.characteristic_id === "accessible_route",
    );
    check(
      routeVerdict?.status === "yes" &&
        routeVerdict.hotelier_confirmed === true &&
        !routeVerdict.synthetic,
      "R3 guest evaluation is yes on the real quoted hotelier response",
    );
    const routeGuest = await guestFact(
      target.id,
      "accessible_route",
      "r3-route",
      {
        parsed: routeQuery,
        quote: route.preview.updates.find(
          (u) => u.characteristic_id === "accessible_route",
        ).quote,
        pattern: /коляск/i,
      },
    );
    check(
      (
        await routeGuest.page
          .getByTestId("verdict-fact-accessible_route")
          .locator("..")
          .innerText()
      ).includes("Подтвердил отель"),
      "R3 actual guest criterion displays the hotel confirmation",
    );
    const routeHeadline = await routeGuest.card
      .locator(".fact-headline")
      .innerText();
    check(
      /отел/i.test(routeHeadline) && !/по карте/i.test(routeHeadline),
      "R3 real hotelier route headline identifies the hotel statement, not a map calculation",
    );
    report.cases.r3 = {
      question_id: routeQuestion.id,
      answer_id: route.accepted.id,
      facts: route.facts,
      verdict: routeVerdict,
    };
    phase = "R4-wifi-question-and-partial";
    const wifiQuery = query("connectivity"),
      wifiPage = await surface({ parsed: wifiQuery });
    currentPage = wifiPage;
    await wifiPage.goto(
      new URL(`/hotel/${encodeURIComponent(target.id)}`, stand).href,
    );
    await wifiPage.getByTestId("ask-connectivity").first().click();
    await wifiPage.getByTestId("ask-confirm").waitFor();
    await capture(wifiPage, "r4-wifi-ask-before", wifiPage.getByRole("dialog"));
    const askResponse = wifiPage.waitForResponse(
      (r) =>
        r.request().method() === "POST" &&
        new URL(r.url()).pathname === "/api/questions",
    );
    await wifiPage.getByTestId("ask-confirm").click();
    const wifiQuestion = await responseJson(
      await askResponse,
      "R4 guest Wi-Fi ask",
    );
    check(
      wifiQuestion.hotel_id === target.id &&
        wifiQuestion.characteristic_ids.includes("connectivity"),
      "R4 guest Wi-Fi question succeeds for the selected hotel/CID",
    );
    currentPage = operator;
    await select(operator, target.id, wifiQuestion);
    const wifi = await operatorConfirm(
      operator,
      target.id,
      wifiQuestion,
      texts.wifi,
      "r4-wifi",
    );
    check(
      wifi.facts.some(
        (f) =>
          f.value.wifi_in_rooms === true &&
          f.value.fee === "free" &&
          f.value.download_mbps == null &&
          f.value.upload_mbps == null,
      ),
      "R4 confirms Wi-Fi in rooms and fee without inventing speed",
    );
    const wifiDetail = await api(
        `/api/hotels/${encodeURIComponent(target.id)}/evaluate`,
        wifiQuery,
      ),
      wifiVerdict = wifiDetail.verdicts.find(
        (v) => v.characteristic_id === "connectivity",
      );
    check(
      wifiVerdict?.status === "partial" && !wifiVerdict.hotelier_confirmed,
      "R4 a 30 Mbps criterion remains partial, not a hotel-confirmed speed promise",
    );
    const wifiGuest = await guestFact(target.id, "connectivity", "r4-wifi", {
      parsed: wifiQuery,
      quote: wifi.preview.updates.find(
        (u) => u.characteristic_id === "connectivity",
      ).quote,
      pattern:
        /Wi-Fi.*номер[\s\S]*скорост[\s\S]*(?:неизвест|не измер|не указан)/i,
    });
    const headline = await wifiGuest.card.locator(".fact-headline").innerText();
    check(
      !/\b30\s*Мбит/i.test(headline),
      "R4 guest headline has no false 30 Mbps claim",
    );
    report.cases.r4 = {
      question_id: wifiQuestion.id,
      answer_id: wifi.accepted.id,
      facts: wifi.facts,
      verdict: wifiVerdict,
      guest_headline: headline,
    };
    phase = "T10-calculated-correction";
    currentPage = operator;
    const correctionBefore = await cabinet(target.id);
    const correctionCandidates = correctionBefore.facts.filter(
      (fact) =>
        fact.hotel_id === target.id &&
        !other.characteristic_ids.includes(fact.characteristic_id) &&
        catalogue.some(
          (spec) =>
            spec.id === fact.characteristic_id &&
            spec.hotelier_answerable === true,
        ) &&
        [
          "quiet_night",
          "sea_view",
          "mountain_view",
          "sunset_view",
          "morning_sun",
        ].includes(fact.characteristic_id) &&
        fact.status === "computed" &&
        !fact.synthetic &&
        typeof fact.value?.available === "boolean" &&
        fact.evidence.some((evidence) => evidence.kind === "calculation"),
    );
    check(
      correctionCandidates.length > 0,
      "T10 has an actual answerable non-synthetic calculated witness outside the control pending CID; no fact is fabricated",
    );
    await operator.goto(new URL("/hotelier", stand).href);
    await operator.getByTestId("hotelier-hotel").selectOption(target.id);
    const renderedCorrectionIds = await until(
      () =>
        operator
          .getByTestId("correct-calculated-fact")
          .evaluateAll((buttons) => buttons.map((button) => button.dataset.factId)),
      (factIds) => correctionCandidates.some((fact) => factIds.includes(fact.id)),
      "T10 actual calculated witness has a rendered correction button",
    );
    const correctionFact = correctionCandidates.find((fact) =>
      renderedCorrectionIds.includes(fact.id),
    );
    const correctionCard = operator.getByTestId(
      `fact-card-${correctionFact.characteristic_id}`,
    );
    const correctionButton = correctionCard.locator(
      `[data-testid="correct-calculated-fact"][data-fact-id="${correctionFact.id}"]`,
    );
    await capture(operator, "t10-calculated-before", correctionCard);
    const correctionPath = `/api/hotelier/${encodeURIComponent(target.id)}/questions/${correctionFact.characteristic_id}`;
    const correctionPosts = [];
    const recordCorrectionPost = (request) => {
      if (request.method() === "POST")
        correctionPosts.push(new URL(request.url()).pathname);
    };
    operator.on("request", recordCorrectionPost);
    const correctionResponse = operator.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname === correctionPath,
    );
    await correctionButton.click();
    const correctionHttp = await correctionResponse;
    const correctionBody = correctionHttp.request().postDataJSON();
    assert.deepEqual(
      correctionBody,
      { scope: correctionFact.scope, fact_id: correctionFact.id },
      "T10 UI posts the exact selected fact and scope to the same hotel/CID endpoint",
    );
    const correctionQuestion = await responseJson(correctionHttp, "T10 correction");
    check(
      correctionQuestion.hotel_id === target.id &&
        isDeepStrictEqual(correctionQuestion.characteristic_ids, [
          correctionFact.characteristic_id,
        ]) &&
        correctionQuestion.status === "pending" &&
        isDeepStrictEqual(
          correctionQuestion.hypothesis_update?.scope,
          correctionFact.scope,
        ) &&
        correctionQuestion.hypothesis_update?.value.available ===
          correctionFact.value.available,
      "T10 returned form retains the actual selected hotel, CID, scope and Boolean proposition",
    );
    const existed = allQuestions(correctionBefore).some(
      (question) => question.id === correctionQuestion.id,
    );
    check(
      existed || correctionQuestion.reason === "low_confidence",
      "T10 explicit operator action does not create a false guest question",
    );
    await until(
      () =>
        operator
          .locator(
            `[data-testid="hotelier-question-select"] option[value="${correctionQuestion.id}"]`,
          )
          .count(),
      (count) => count === 1,
      `T10 returned Question ${correctionQuestion.id} is in the actual queue`,
    );
    await until(
      () => operator.getByTestId("hotelier-question-select").inputValue(),
      (id) => id === correctionQuestion.id,
      "T10 same form selects the exact returned question without a reload",
    );
    check(
      (await operator.getByTestId("hotelier-question").innerText()).includes(
        correctionQuestion.hypothesis,
      ) && (await operator.getByTestId("hotelier-answer").inputValue()) === "",
      "T10 displays the selected proposition in an empty operator answer form",
    );
    await capture(
      operator,
      "t10-calculated-form",
      operator.getByTestId("hotelier-question"),
    );
    const correctionAfter = await cabinet(target.id);
    assert.deepEqual(
      correctionAfter.answers.map((answer) => answer.id).sort(),
      correctionBefore.answers.map((answer) => answer.id).sort(),
      "T10 click alone creates no Answer or confirmation",
    );
    assert.deepEqual(
      correctionAfter.claims.map((claim) => claim.id).sort(),
      correctionBefore.claims.map((claim) => claim.id).sort(),
      "T10 click alone creates no Claim",
    );
    check(
      pending(correctionAfter).some((question) => question.id === other.id),
      "T10 leaves the control pending question open",
    );
    operator.off("request", recordCorrectionPost);
    check(
      correctionPosts.length === 1 && correctionPosts[0] === correctionPath,
      "T10 only posts the selected correction request; no answer parse/confirm is sent",
    );
    report.cases.t10 = {
      hotel_id: target.id,
      witness: subset(correctionFact, [
        "id",
        "characteristic_id",
        "scope",
        "status",
        "synthetic",
        "value",
        "supporting_claim_ids",
      ]),
      calculation_evidence: correctionFact.evidence
        .filter((evidence) => evidence.kind === "calculation")
        .map((evidence) => subset(evidence, ["id", "kind", "source_ids"])),
      post: { path: correctionPath, body: correctionBody },
      question: correctionQuestion,
      control_pending_question_id: other.id,
      no_answer_or_claim_created: true,
      no_reload_after_click: true,
    };
    phase = "existing-synthetic-label";
    const demoSearch = await api("/api/search", {
      text: "с собакой 4 кг",
      parsed: query("pets"),
      limit: 40,
      compact: true,
    });
    const candidates = demoSearch.results.filter((hit) =>
      hit.verdicts.some(
        (verdict) => verdict.characteristic_id === "pets" && verdict.synthetic,
      ),
    );
    let demo = null,
      demoProof = [];
    for (const candidate of candidates) {
      const verdict = candidate.verdicts.find(
        (item) => item.characteristic_id === "pets",
      );
      const detail = await api(
        `/api/hotels/${encodeURIComponent(candidate.hotel.id)}`,
      );
      const proof = [];
      for (const fact of detail.facts.filter(
        (fact) =>
          fact.characteristic_id === "pets" &&
          fact.synthetic &&
          verdict.fact_ids.includes(fact.id),
      )) {
        for (const claimId of fact.supporting_claim_ids) {
          const claim = detail.claims.find(
            (claim) =>
              claim.id === claimId &&
              claim.hotel_id === candidate.hotel.id &&
              claim.characteristic_id === "pets" &&
              claim.synthetic &&
              claim.source === "hotelier_simulator",
          );
          if (!claim) continue;
          const evidence = claim.evidence.filter(
            (item) =>
              item.kind === "simulation" &&
              item.source_ids.includes("hotelier_simulator") &&
              fact.evidence.some(
                (copy) =>
                  copy.id === item.id &&
                  copy.kind === item.kind &&
                  copy.source_ids.includes("hotelier_simulator"),
              ),
          );
          if (evidence.length)
            proof.push({
              fact_id: fact.id,
              claim_id: claim.id,
              source: claim.source,
              evidence_ids: evidence.map((item) => item.id),
            });
        }
      }
      if (proof.length) {
        demo = candidate;
        demoProof = proof;
        break;
      }
    }
    check(
      !!demo && demoProof.length > 0,
      "Existing synthetic pet verdict is backed by its actual hotelier_simulator claim and simulation evidence, not merely an arbitrary synthetic flag",
    );
    const demoVerdict = demo.verdicts.find(
      (verdict) => verdict.characteristic_id === "pets",
    );
    if (demoVerdict.basis !== "hotelier")
      report.limitations.push(
        `Existing simulator fixture has basis=${demoVerdict.basis}; simulator origin is proven independently through actual supporting claims/evidence, and basis is retained unchanged in the report.`,
      );
    const demoGuest = await guestFact(demo.hotel.id, "pets", "existing-demo", {
      parsed: query("pets"),
    });
    check(
      /Ответ отеля · демо/.test(await demoGuest.card.innerText()),
      "Existing synthetic reply has the explicit demo label in actual guest UI",
    );
    check(
      !(await demoGuest.card.getByTestId("evidence-pets").innerText()).includes(
        "Подтвердил отель",
      ),
      "Existing synthetic evidence never receives a real hotel-confirmation label",
    );
    report.cases.synthetic = {
      existing_fixture: true,
      hotel_id: demo.hotel.id,
      run_id: demoSearch.run_id,
      verdict: demoVerdict,
      basis_observed: demoVerdict.basis,
      supporting_proof: demoProof,
      no_synthetic_writes: true,
    };
    check(
      report.forbidden_mutations.length === 0,
      "No operation outside the strict localhost:8081 mutation allowlist occurred",
    );
    check(
      report.browser_errors.length === 0,
      "No browser exceptions occurred during the actual action workflow",
    );
    report.status = "pass";
  } catch (error) {
    report.status = "fail";
    report.failure_phase = phase;
    report.error = clean(error.message);
    report.error_stack = clean(error.stack || error.message);
    if (currentPage && !currentPage.isClosed()) {
      try {
        await capture(currentPage, "failure");
      } catch {
        /* Keep the original semantic error. */
      }
    }
  } finally {
    await Promise.allSettled([...contexts].map((context) => context.close()));
    if (browser) await browser.close();
    token = "";
    report.finished_at = new Date().toISOString();
    await writeFile(reportPath, JSON.stringify(report, null, 2) + "\n");
  }
  if (report.status !== "pass")
    throw new Error(
      `R3 actions failed in ${report.failure_phase}: ${report.error}; report=${reportPath}`,
    );
  return { ...report, report_path: reportPath };
}

if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(resolve(process.argv[1])).href
) {
  runPanelR3Actions({
    base: process.env.OKNO_BASE_URL,
    artifacts: process.env.OKNO_PANEL_R3_ARTIFACTS,
  })
    .then((report) =>
      console.log(
        JSON.stringify({
          status: report.status,
          checks: report.checks.length,
          captures: report.captures.length,
          report: report.report_path,
        }),
      ),
    )
    .catch((error) => {
      console.error(error.message);
      process.exitCode = 1;
    });
}
