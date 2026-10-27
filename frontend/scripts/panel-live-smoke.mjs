import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

// Run only after the integrated application has been rebuilt:
// OKNO_BASE_URL=http://localhost:8080 node scripts/panel-live-smoke.mjs
// Real APIs throughout: no response fixtures, no answers, questions, passports,
// refreshes or compilations. Preview itself is a disallowed answer POST.
const base = new URL(process.env.OKNO_BASE_URL || "http://localhost:8080");
assert(["http:", "https:"].includes(base.protocol) && !base.username && !base.password, "Invalid stand URL");
base.pathname = "/";
base.search = "";
base.hash = "";
const artifacts = fileURLToPath(new URL("../../runtime/panel-live", import.meta.url));
const chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
assert(existsSync(chrome), "System Google Chrome is required; no browser download");
await mkdir(artifacts, { recursive: true });
const report = {
  status: "running",
  real_api: true,
  started_at: new Date().toISOString(),
  stand_origin: base.origin,
  checks: [],
  captures: [],
  search_run_ids: [],
  targets: {},
  allowed_posts: {},
  forbidden_mutation_attempts: [],
  blocked_providers: [],
  blocked_external_navigation: [],
  errors: [],
  limitations: [
    "No/Unknown acceptance is covered by existing memory and fixture tests. Live checks stop before the answer-preview POST.",
    "Map route minutes are checked against the actual geo API. Canvas-only glyph text is captured for visual review when it has no DOM representation.",
  ],
};
const secrets = new Set();
const contexts = new Set();
const pending = new Set();
const runIds = new Set();
const captures = new Set();
const queries = {
  masha: "тихий номер с видом на закат недалеко от моря",
  sea: "море недалеко",
  stroller: "Едем с коляской",
  route: "до пляжа не больше 10 минут, без лестниц, едем с коляской",
  wifi: "быстрый интернет не меньше 30 Мбит",
  nightlife: "кафе и бары рядом",
};
Object.values(queries).forEach((text) => secrets.add(text));
function redact(value) {
  let text = String(value);
  for (const secret of [...secrets].sort((a, b) => b.length - a.length))
    if (secret) text = text.split(secret).join("[private text redacted]");
  return text.replace(/https?:\/\/[^\s"'<>]+/g, (url) => {
    try {
      const parsed = new URL(url);
      return parsed.origin + parsed.pathname + (parsed.search ? "?[redacted]" : "");
    } catch {
      return "[URL redacted]";
    }
  }).replace(/\bBearer\s+\S+/gi, "Bearer [redacted]")
    .replace(/\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_.-]+/g, "[token redacted]");
}
function check(ok, label) {
  assert.ok(ok, label);
  report.checks.push(label);
}
function provider(hostname) {
  const host = hostname.toLowerCase();
  return /(^|\.)yandex\.[a-z.]+$/.test(host) ||
    /(^|\.)ya\.ru$/.test(host) ||
    /(^|\.)mapillary\.[a-z.]+$/.test(host);
}
function allowedPost(path) {
  return ["/api/search", "/api/compare", "/api/hotelier/login", "/api/hotelier/logout"].includes(path) ||
    /^\/api\/hotels\/[^/]+\/evaluate$/.test(path);
}
function mutationGuard(method, url) {
  if (["GET", "HEAD", "OPTIONS"].includes(method)) return true;
  if (method === "POST" && url.origin === base.origin && allowedPost(url.pathname)) {
    report.allowed_posts[url.pathname] = (report.allowed_posts[url.pathname] || 0) + 1;
    return true;
  }
  report.forbidden_mutation_attempts.push({ method, path: url.pathname });
  return false;
}
function rememberSearch(result) {
  check(typeof result.run_id === "string" && result.run_id.length > 0, "Actual search returned a durable run id");
  runIds.add(result.run_id);
}
async function api(path, body) {
  const url = new URL(path, base);
  const method = body === undefined ? "GET" : "POST";
  check(url.origin === base.origin && !provider(url.hostname), "API request remains on the stand");
  check(mutationGuard(method, url), "API mutation is explicitly permitted");
  if (path === "/api/search" && body?.text) secrets.add(body.text);
  const response = await fetch(url, {
    method,
    redirect: "manual",
    credentials: "omit",
    signal: AbortSignal.timeout(45000),
    ...(body === undefined ? {} : { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
  });
  check(response.ok, method + " " + url.pathname + " succeeded (" + response.status + ")");
  const value = await response.json();
  if (url.pathname === "/api/search") rememberSearch(value);
  return value;
}
const browser = await chromium.launch({ headless: true, executablePath: chrome });
async function surface(width, scheme = "light", height = width < 500 ? 844 : 1000) {
  const context = await browser.newContext({
    viewport: { width, height },
    colorScheme: scheme,
    serviceWorkers: "block",
  });
  contexts.add(context);
  // Context-wide interception precedes page creation/navigation and covers popups.
  await context.route("**/*", async (route) => {
    const request = route.request(), url = new URL(request.url());
    if (!mutationGuard(request.method(), url)) return route.abort("blockedbyclient");
    if (provider(url.hostname)) {
      report.blocked_providers.push({ host: url.hostname, resource: request.resourceType() });
      return route.abort("blockedbyclient");
    }
    if (request.isNavigationRequest() && url.origin !== base.origin) {
      report.blocked_external_navigation.push({ host: url.hostname });
      return route.abort("blockedbyclient");
    }
    return route.continue();
  });
  context.on("page", (page) => {
    page.on("pageerror", (error) => report.errors.push(redact(error.message)));
    page.on("response", (response) => {
      const request = response.request();
      if (request.method() !== "POST" || new URL(response.url()).pathname !== "/api/search" || !response.ok()) return;
      const task = response.json().then(rememberSearch).catch(() => report.errors.push("Search response could not be validated"));
      pending.add(task);
      task.finally(() => pending.delete(task));
    });
  });
  const page = await context.newPage();
  page.setDefaultTimeout(25000);
  return { context, page };
}
async function close(surface) {
  await surface.context.close();
  contexts.delete(surface.context);
}
async function capture(page, name) {
  check(!captures.has(name), "Screenshot name is unique: " + name);
  captures.add(name);
  await page.screenshot({ path: resolve(artifacts, name + ".png"), fullPage: true, animations: "disabled" });
  report.captures.push(name + ".png");
}
async function cleanVisible(page, label) {
  const text = await page.locator("body").innerText();
  check(!/hotelier_simulator|synthetic-demo|demo truth|симуляция|фикстур|Copernicus|Monte Carlo|geo_engine/i.test(text),
    label + ": first-level copy has no technical fixture language");
  check(!/OSM\s+osm-|osm-(?:way|node|relation)-\d+/i.test(text), label + ": no visible OSM ids");
  check(!/(?:над[её]жност[ьи]|уверенност[ьи]|confidence)[^.\n]{0,35}\d+\s*%|\d+\s*%\s*(?:над[её]жност|уверенност)/i.test(text),
    label + ": no numerical confidence claims");
  check(!/низк(?:ой|ая|ую)\s+уверенност/i.test(text),
    label + ": uncertainty uses guest language rather than model-confidence terms");
  check(!/(?:≈\s*)?(\d+)\s*мин\s*вместо\s*(?:≈\s*)?\1\s*мин/i.test(text), label + ": no equal rounded-minute comparison");
}
async function home(page, scheme) {
  await page.goto(base.href, { waitUntil: "domcontentloaded" });
  await page.getByTestId("search-input").waitFor();
  check(await page.getByTestId("search-input").inputValue() === "", "Fresh home input is empty");
  await page.locator("[data-theme=" + scheme + "]").waitFor();
  check(await page.evaluate(() => localStorage.getItem("okno-theme")) === null, "System theme has no localStorage override");
}
async function searchUI(page, text, { masha = false } = {}) {
  secrets.add(text);
  if (!(await page.getByTestId("search-input").count())) await page.goto(base.href);
  const response = page.waitForResponse((r) => new URL(r.url()).pathname === "/api/search" &&
    r.request().method() === "POST" && !r.request().postDataJSON()?.similar_to);
  if (masha) await page.getByTestId("masha-example").click();
  else {
    await page.getByTestId("search-input").fill(text);
    await page.getByTestId("search-submit").click();
  }
  const actual = await response;
  check(actual.ok(), "UI search returned success");
  const result = await actual.json();
  rememberSearch(result);
  check(result.results.length > 0, "Real UI search has results");
  await page.locator(".result-card").first().waitFor();
  return result;
}
const criteria = (parsed) => [...(parsed.must || []), ...(parsed.rooms || []).flatMap((room) => room.must || [])];
const allVerdicts = (hit) => [...(hit.verdicts || []), ...(hit.room_plan || []).flatMap((room) => room.verdicts || [])];
function fact(detail, cid, predicate = () => true) {
  const candidates = detail.facts.filter((row) => row.characteristic_id === cid && !row.synthetic &&
    ["computed", "confirmed"].includes(row.status) && row.value && row.supporting_claim_ids?.length &&
    row.evidence?.length && (!row.valid_until || Date.parse(row.valid_until) > Date.now()) && predicate(row.value));
  check(candidates.length > 0, "Real supported fresh fact exists: " + cid);
  return candidates.sort((a, b) => b.confidence - a.confidence)[0];
}
function verdict(detail, cid) {
  const actual = detail.verdicts.find((row) => row.characteristic_id === cid);
  check(Boolean(actual), "Evaluation includes " + cid);
  return actual;
}
const rounded = (value) => value > 0 ? Math.max(1, Math.floor(value + 0.5)) : 0;
const firstMinutes = (text) => Number(text.match(/(?:≈\s*)?(\d+)\s*мин/)?.[1]);
async function noDemoConfirmation(page, result) {
  let demo = 0;
  for (const hit of result.results) {
    const required = allVerdicts(hit).filter((row) => row.required);
    for (const row of allVerdicts(hit).filter((row) => row.synthetic)) {
      demo++;
      check(!row.hotelier_confirmed, "Real API demo verdict is not hotelier-confirmed");
    }
    const count = required.filter((row) => row.status === "yes" && row.hotelier_confirmed && !row.synthetic && !row.disputed).length;
    const card = page.getByTestId("hotel-card-" + hit.hotel.id);
    const summary = await card.getByTestId("required-summary").innerText();
    check(!/\d+\s*%/.test(await card.innerText()), "Compact real result card has no confidence percentages");
    const shown = Number(summary.match(/(\d+)\s+подтвердил отель/)?.[1] || 0);
    check(shown === count, "Visible confirmed count equals real hotelier flags");
  }
  return demo;
}
async function openHotel(page, id) {
  await page.goto(new URL("/hotel/" + encodeURIComponent(id), base).href, { waitUntil: "domcontentloaded" });
  await page.locator(".hotel-fit").waitFor();
  await page.getByTestId("requested-facts").waitFor();
}
async function openCompare(page, ids) {
  await page.goto(new URL("/compare?ids=" + ids.map(encodeURIComponent).join(","), base).href);
  await page.locator(".comparison-table tbody tr").first().waitFor();
  const onlyDiff = page.getByRole("checkbox", { name: "Только различия" });
  if (await onlyDiff.isChecked()) await onlyDiff.uncheck();
}
function publicStep(step) {
  check(JSON.stringify(step.input) === JSON.stringify({ summary: "Запрос гостя" }), "Public search step input is redacted");
  check(Object.keys(step.output || {}).every((key) => key === "total"), "Public search output contains only a count");
  check(!step.evidence_refs?.length, "Public search step has no private evidence refs");
  check(["Запрос гостя", "Подбор отелей"].includes(step.name), "Public search step uses a neutral name");
}
function noPrivateText(value) {
  const raw = JSON.stringify(value);
  for (const text of secrets) check(!raw.includes(text), "Public run response contains no submitted private text");
}
async function publicRuns() {
  const list = await api("/api/runs");
  const searches = list.filter((row) => row.kind === "search");
  check(searches.length > 0, "Public run list includes actual searches");
  noPrivateText(searches);
  for (const row of searches) for (const step of row.steps || []) publicStep(step);
  check([...runIds].some((id) => searches.some((row) => row.id === id)), "Own live search is represented in the public run list");
  for (const id of [...runIds]) {
    const path = "/api/runs/" + encodeURIComponent(id);
    const detail = await api(path);
    check(detail.kind === "search" && detail.steps.length > 0, "Own live run detail has real search steps");
    noPrivateText(detail);
    detail.steps.forEach(publicStep);
    // Anonymous fetch deliberately has neither the cabinet cookie nor Bearer auth.
    const response = await fetch(new URL(path + "/stream", base), {
      redirect: "manual", credentials: "omit", signal: AbortSignal.timeout(15000),
    });
    check(response.ok && response.headers.get("content-type")?.includes("text/event-stream"), "Anonymous real run SSE is available");
    const stream = await response.text();
    noPrivateText(stream);
    const records = stream.split(/\r?\n\r?\n/).map((block) => {
      const event = block.match(/^event: (.+)$/m)?.[1];
      const data = block.match(/^data: (.+)$/m)?.[1];
      return data ? { event, data: JSON.parse(data) } : null;
    }).filter(Boolean);
    check(records.some((record) => record.event === "step") && records.some((record) => record.event === "done"),
      "SSE includes actual redacted steps and a completed terminator");
    records.filter((record) => record.event === "step").forEach((record) => publicStep(record.data));
  }
}
let currentPage;
try {
  const status = await api("/api/status");
  check(status.hotel_count > 0, "Stand has a real hotel catalogue");
  const hotels = await api("/api/hotels");
  const specs = await api("/api/characteristics");
  const primorskaya = hotels.find((row) => row.name.trim().toLowerCase() === "приморская");
  const terminal = hotels.find((row) => /терминал.*адлер|terminal.*adler/i.test(row.name));
  check(Boolean(primorskaya), "Primorskaya target is present in the real API");
  check(Boolean(terminal), "Terminal Adler target is present in the real API");
  report.targets.primorskaya = primorskaya.id;
  report.targets.terminal_adler = terminal.id;

  const olga = await surface(390);
  currentPage = olga.page;
  await home(olga.page, "light");
  await cleanVisible(olga.page, "Olga home");
  await capture(olga.page, "olga-home-light-390");
  const masha = await searchUI(olga.page, queries.masha, { masha: true });
  check(/\/search$/.test(new URL(olga.page.url()).pathname), "Masha chip starts search in one click");
  let demos = await noDemoConfirmation(olga.page, masha);
  await cleanVisible(olga.page, "Olga results");
  await capture(olga.page, "olga-search-light-390");
  const ask = olga.page.locator('button[data-testid^="ask-"]:not(:disabled)').first();
  check(await ask.count() === 1, "Real result offers an Ask control");
  const beforeAsk = report.forbidden_mutation_attempts.length;
  await ask.click();
  const dialog = olga.page.getByRole("dialog");
  await dialog.waitFor();
  check(/здесь.*паспорт|паспорт.*здесь/is.test(await dialog.innerText()), "Ask dialog explains where the answer appears");
  await olga.page.getByRole("button", { name: "Отмена", exact: true }).click();
  check(await dialog.count() === 0 && report.forbidden_mutation_attempts.length === beforeAsk, "Ask and Cancel make no mutation attempt");
  const sea = await searchUI(olga.page, queries.sea);
  check(criteria(sea.parsed).some((row) => row.characteristic_id === "beach_walk"), "Colloquial sea-nearby is a must criterion");
  await cleanVisible(olga.page, "Sea-nearby search");
  await close(olga);

  const stroller = await api("/api/search", { text: queries.stroller, limit: 40 });
  const stairCriterion = criteria(stroller.parsed).find((row) =>
    ["beach_walk", "accessible_route"].includes(row.characteristic_id) &&
    (row.no_stairs || row.field === "has_steps" && row.op === "eq" && row.value === false));
  check(Boolean(stairCriterion),
  "Actual stroller trip makes a stair-free route mandatory");
  const stairsDetail = await api("/api/hotels/" + encodeURIComponent(primorskaya.id) + "/evaluate", stroller.parsed);
  const accessFact = fact(stairsDetail, stairCriterion.characteristic_id, (value) => value.has_steps === true);
  const accessVerdict = verdict(stairsDetail, stairCriterion.characteristic_id);
  check(accessVerdict.status === "no" && /лестниц|ступен|подъ[её]м/i.test(accessVerdict.text),
    "Actual Primorskaya evaluates as known no with route barriers");
  check(accessFact.value.has_steps === true, "Primorskaya has actual stairs evidence");

  const igor = await surface(1440);
  currentPage = igor.page;
  await home(igor.page, "light");
  const routes = await searchUI(igor.page, queries.route);
  const routeDetail = await api("/api/hotels/" + encodeURIComponent(primorskaya.id) + "/evaluate", routes.parsed);
  const familyVerdict = verdict(routeDetail, "family_nearby");
  check(familyVerdict.status === "partial" &&
    /Пока неточно — лучше уточнить у отеля/.test(familyVerdict.text + " " + (familyVerdict.caveat || "")),
  "Actual low-confidence family evidence remains partial with a human explanation");
  const routeFact = fact(routeDetail, "beach_walk", (value) => typeof value.minutes === "number");
  check(Number.isFinite(routeFact.value.return_ascent_m) && routeFact.value.return_ascent_m > 0,
    "Primorskaya has an actual positive return ascent");
  const geo = await api("/api/hotels/" + encodeURIComponent(primorskaya.id) + "/geo");
  const walk = geo.route?.features.find((row) => row.geometry?.type === "LineString" && typeof row.properties?.minutes === "number");
  check(Boolean(walk), "Actual Primorskaya has route geometry and minute metadata");
  const minutes = rounded(routeFact.value.minutes);
  check(rounded(walk.properties.minutes) === minutes, "Actual fact and map route use the same rounded minutes");
  const apiVerdict = verdict(routeDetail, "beach_walk");
  check(firstMinutes(apiVerdict.text) === minutes, "Actual API beach verdict agrees with minute rounding");
  const listed = routes.results.find((hit) => hit.hotel.id === primorskaya.id);
  if (listed) {
    const beach = igor.page.getByTestId("hotel-card-" + primorskaya.id).locator(".verdict-list > li")
      .filter({ hasText: /пляж/i }).first();
    const phrase = beach.getByTestId("compact-number-phrase");
    check(await phrase.count() === 1 && firstMinutes(await phrase.innerText()) === minutes, "Search result card minutes agree with the API");
  }
  report.minute_checks = {
    hotel_id: primorskaya.id,
    rounded_minutes: minutes,
    return_ascent_m: routeFact.value.return_ascent_m,
    search_card_present: Boolean(listed),
  };
  await openHotel(igor.page, primorskaya.id);
  const routeCard = igor.page.getByTestId("fact-card-beach_walk").first();
  await routeCard.waitFor();
  check(firstMinutes(await routeCard.locator(".fact-headline").innerText()) === minutes, "Hotel fact card minutes agree with the API");
  check(/Пока неточно — лучше уточнить у отеля/.test(await igor.page.locator(".hotel-fit").innerText()),
    "Primorskaya family uncertainty is visible in guest language");
  await igor.page.getByTestId("fact-map-beach_walk").click();
  const routeContext = igor.page.getByTestId("hotel-map-context");
  await routeContext.waitFor();
  check(await routeContext.getAttribute("data-fact-id") === routeFact.id, "Selected map context refers to the real beach route fact");
  check(!/наличие\s+окна|условия\s+номера/i.test(await routeContext.innerText()),
    "Beach route map context has no unrelated room or window caveat");
  await igor.page.getByTestId("travel-map").locator("canvas").first().waitFor();
  const mapText = await igor.page.getByTestId("travel-map").innerText();
  report.minute_checks.map_dom_minutes_available = /(?:≈\s*)?\d+\s*мин/.test(mapText);
  if (report.minute_checks.map_dom_minutes_available)
    check(firstMinutes(mapText) === minutes, "Visible DOM map minutes agree with the API");
  await cleanVisible(igor.page, "Igor hotel");
  await capture(igor.page, "igor-hotel-light-1440");
  const compareIds = [primorskaya.id, routes.results.find((hit) => hit.hotel.id !== primorskaya.id)?.hotel.id];
  check(Boolean(compareIds[1]), "A second actual hotel exists for comparison");
  const compared = await api("/api/compare", { ids: compareIds, parsed: routes.parsed });
  const comparedHotel = compared.hotels.find((row) => row.hotel.id === primorskaya.id);
  check(firstMinutes(verdict(comparedHotel, "beach_walk").text) === minutes, "Actual comparison API minutes agree");
  await openCompare(igor.page, compareIds);
  const beachName = specs.find((row) => row.id === "beach_walk");
  check(Boolean(beachName), "Real catalogue has the beach characteristic");
  const beachRow = igor.page.locator(".comparison-table tbody tr").filter({
    has: igor.page.locator("th", { hasText: beachName.display_name || beachName.name_ru }),
  });
  await beachRow.waitFor();
  check(await beachRow.count() === 1, "Comparison includes the actual beach row");
  const firstCell = beachRow.locator("td").first();
  const compareVerdictText = await firstCell.locator(".compare-verdict").innerText();
  report.minute_checks.compare_verdict_minutes_available = /(?:≈\s*)?\d+\s*мин/.test(compareVerdictText);
  if (report.minute_checks.compare_verdict_minutes_available)
    check(firstMinutes(compareVerdictText) === minutes, "Comparison verdict minutes agree");
  check(firstMinutes(await firstCell.locator(".compare-summary").innerText()) === minutes, "Comparison summary minutes agree");
  await cleanVisible(igor.page, "Igor compare");
  await capture(igor.page, "igor-compare-light-1440");
  await close(igor);

  const wifiSearch = await api("/api/search", { text: queries.wifi, limit: 40 });
  const wifiCandidates = [...new Set([
    ...hotels.filter((row) => /wlan|wifi|wi-fi/i.test(String(row.tags?.internet_access))).map((row) => row.id),
    ...wifiSearch.results.map((hit) => hit.hotel.id),
  ])];
  let wifiDetail;
  for (const id of wifiCandidates) {
    const candidate = await api("/api/hotels/" + encodeURIComponent(id) + "/evaluate", wifiSearch.parsed);
    if (candidate.facts.some((row) => row.characteristic_id === "connectivity" && !row.synthetic &&
      row.value?.wifi_reported === true && row.value.download_mbps === null &&
      ["computed", "confirmed"].includes(row.status) && row.supporting_claim_ids?.length && row.evidence?.length)) {
      wifiDetail = candidate;
      break;
    }
  }
  check(Boolean(wifiDetail), "A real Wi-Fi-reported hotel with unmeasured speed is present");
  report.targets.wifi = wifiDetail.hotel.id;
  const wifiFact = fact(wifiDetail, "connectivity", (value) => value.wifi_reported === true && value.download_mbps === null);
  check(wifiFact.value.download_mbps === null, "Actual Wi-Fi speed remains null");
  const wifiVerdict = verdict(wifiDetail, "connectivity");
  check(wifiVerdict.status === "partial" && /Wi-Fi указан, скорость не измерена/.test(wifiVerdict.text),
    "Real Wi-Fi presence is partial and does not invent measured Mbps");
  const wifiRanking = await api("/api/search", { text: queries.wifi, parsed: wifiSearch.parsed, limit: 250 });
  const wifiHit = wifiRanking.results.find((hit) => hit.hotel.id === wifiDetail.hotel.id);
  check(Boolean(wifiHit) && wifiHit.group !== "all", "Unmeasured Wi-Fi does not satisfy the numeric must");
  const nightlifeSearch = await api("/api/search", { text: queries.nightlife, limit: 40 });
  const terminalDetail = await api("/api/hotels/" + encodeURIComponent(terminal.id) + "/evaluate", nightlifeSearch.parsed);
  const nightlifeFact = fact(terminalDetail, "nightlife", (value) => Number.isFinite(value.venues_500m) && value.venues_500m < 30);
  check(nightlifeFact.value.venues_500m < 30 && verdict(terminalDetail, "nightlife").status === "no" &&
    /по карте мало/i.test(verdict(terminalDetail, "nightlife").text), "Actual Terminal Adler is known no: too few mapped venues");
  const dina = await surface(1280, "dark", 800);
  currentPage = dina.page;
  await home(dina.page, "dark");
  await dina.page.getByTestId("search-input").fill("с собакой 15 кг");
  await dina.page.getByTestId("group-search-example").click();
  check((await dina.page.getByTestId("search-input").inputValue()).startsWith("с собакой 15 кг\n\n"),
    "Company example appends to Dina's existing text");
  await capture(dina.page, "dina-home-dark-1280");
  await searchUI(dina.page, queries.wifi);
  await openHotel(dina.page, wifiDetail.hotel.id);
  check(/Wi-Fi|скорост/i.test(await dina.page.getByTestId("requested-facts").innerText()), "Dina sees actual Wi-Fi information");
  await cleanVisible(dina.page, "Dina Wi-Fi hotel");
  await capture(dina.page, "dina-wifi-dark-1280");
  await searchUI(dina.page, queries.nightlife);
  await openHotel(dina.page, terminal.id);
  check(/по карте мало/i.test(await dina.page.locator(".hotel-fit").innerText()), "Dina sees Terminal Adler's known negative");
  await cleanVisible(dina.page, "Dina nightlife hotel");
  await capture(dina.page, "dina-terminal-dark-1280");
  await close(dina);

  // Actual synthetic responses must be encountered, so this cannot pass vacuously.
  if (!demos) {
    const demoSurface = await surface(390);
    currentPage = demoSurface.page;
    await home(demoSurface.page, "light");
    const demoSearch = await searchUI(demoSurface.page, "с питомцем, тихий номер");
    demos = await noDemoConfirmation(demoSurface.page, demoSearch);
    await cleanVisible(demoSurface.page, "Actual demo results");
    await capture(demoSurface.page, "actual-demo-light-390");
    await close(demoSurface);
  }
  check(demos > 0, "Actual stand demo verdicts were exercised");

  // Four key paths in both themes at both requested widths, all from real APIs.
  for (const width of [1440, 390]) for (const scheme of ["light", "dark"]) {
    const item = await surface(width, scheme);
    currentPage = item.page;
    await home(item.page, scheme);
    await capture(item.page, "matrix-home-" + scheme + "-" + width);
    const actual = await searchUI(item.page, queries.masha, { masha: true });
    await noDemoConfirmation(item.page, actual);
    await cleanVisible(item.page, "Matrix search");
    await capture(item.page, "matrix-search-" + scheme + "-" + width);
    await openHotel(item.page, actual.results[0].hotel.id);
    await cleanVisible(item.page, "Matrix hotel");
    await capture(item.page, "matrix-hotel-" + scheme + "-" + width);
    const ids = actual.results.slice(0, 2).map((hit) => hit.hotel.id);
    check(ids.length === 2, "Real search has two hotels for the capture matrix");
    await openCompare(item.page, ids);
    await cleanVisible(item.page, "Matrix compare");
    await capture(item.page, "matrix-compare-" + scheme + "-" + width);
    await close(item);
  }

  for (const [width, scheme] of [[1440, "light"], [390, "dark"]]) {
    const marina = await surface(width, scheme);
    currentPage = marina.page;
    await marina.page.goto(new URL("/hotelier", base).href);
    await marina.page.getByTestId("hotelier-login").waitFor();
    secrets.add(await marina.page.getByTestId("hotelier-password").inputValue());
    await capture(marina.page, "marina-login-" + scheme + "-" + width);
    const cabinetResponses = new Map();
    const rememberCabinet = (response) => {
      const path = new URL(response.url()).pathname;
      if (/^\/api\/hotelier\/[^/]+$/.test(path) &&
        !["/api/hotelier/summaries", "/api/hotelier/login"].includes(path) &&
        response.request().method() === "GET" && response.ok())
        cabinetResponses.set(decodeURIComponent(path.split("/").at(-1)), response);
    };
    marina.page.on("response", rememberCabinet);
    await marina.page.getByTestId("hotelier-login").click();
    await marina.page.getByTestId("hotelier-question-queue").waitFor();
    const selected = await marina.page.getByTestId("hotelier-hotel").inputValue();
    check(cabinetResponses.has(selected), "Cabinet was loaded from the actual private GET endpoint");
    const cabinet = await cabinetResponses.get(selected).json();
    marina.page.off("response", rememberCabinet);
    const chosen = hotels.find((row) => row.id === selected);
    check(Boolean(chosen) && chosen.name !== "Островок", "Cabinet default is a real hotel other than Ostrovok");
    check(cabinet.hotel.id === selected && cabinet.questions.length > 0, "Default cabinet has actual pending questions");
    report.targets["marina_" + width] = selected;
    const codes = new RegExp("\\b(?:" + specs.map((row) => row.id.replace(/[.*+?^$()|[\]\\]/g, "\\$&")).join("|") + ")\\b");
    const questionTexts = cabinet.questions.map((row) => [row.text, row.hypothesis || ""].join("\n")).join("\n") + "\n" +
      await marina.page.getByTestId("hotelier-question-queue").innerText() + "\n" +
      await marina.page.locator(".question-hypothesis").innerText();
    check(!/\{\s*['"][^'"]+['"]\s*:/.test(questionTexts) && !codes.test(questionTexts), "Actual cabinet questions have no raw dictionary or characteristic ids");
    const no = marina.page.getByRole("button", { name: "Нет", exact: true });
    const unknown = marina.page.getByRole("button", { name: "Не знаю", exact: true });
    check(await no.count() === 1 && await unknown.count() === 1, "Actual pending question offers No and Unknown controls");
    await no.click();
    check(await marina.page.getByTestId("hotelier-answer").inputValue() === "Нет", "No control fills text without parsing or submission");
    check(await marina.page.getByTestId("answer-preview").isEnabled(), "No text is ready for explicit preview");
    await unknown.click();
    check(await marina.page.getByTestId("hotelier-answer").inputValue() === "Не знаю", "Unknown control fills text without parsing or submission");
    check(await marina.page.getByTestId("answer-preview").isEnabled(), "Unknown text is ready for explicit preview");
    check(await marina.page.getByTestId("answer-fields").count() === 0 &&
      await marina.page.getByTestId("answer-confirm").count() === 0, "Live cabinet stops before preview and confirmation");
    await capture(marina.page, "marina-cabinet-" + scheme + "-" + width);
    await marina.page.getByRole("button", { name: "Выйти", exact: true }).click();
    await marina.page.getByTestId("hotelier-login").waitFor();
    check(await marina.page.getByTestId("hotelier-answer").count() === 0, "Logout clears the private cabinet");
    await marina.page.goto(new URL("/agent", base).href);
    await marina.page.getByTestId("agent-summary").waitFor();
    await capture(marina.page, "marina-agent-" + scheme + "-" + width);
    await close(marina);
  }
  await Promise.all([...pending]);
  await publicRuns();
  check(report.forbidden_mutation_attempts.length === 0, "Zero forbidden POST/PUT/PATCH/DELETE attempts");
  check(report.blocked_external_navigation.length === 0, "No external provider page navigation was attempted");
  check(report.errors.length === 0, "No browser exceptions");
  report.status = "pass";
} catch (error) {
  report.status = "fail";
  report.error = redact(error.message);
  if (currentPage && !currentPage.isClosed()) {
    try { await capture(currentPage, "failure"); } catch { /* Preserve the original failure. */ }
  }
} finally {
  await Promise.allSettled([...contexts].map((context) => context.close()));
  await Promise.race([browser.close(), new Promise((done) => setTimeout(done, 5000))]);
  report.search_run_ids = [...runIds].sort();
  report.finished_at = new Date().toISOString();
  await writeFile(resolve(artifacts, "report.json"), JSON.stringify(report, null, 2) + "\n");
}
console.log(JSON.stringify({
  status: report.status,
  checks: report.checks.length,
  captures: report.captures.length,
  search_runs: report.search_run_ids.length,
  forbidden_mutation_attempts: report.forbidden_mutation_attempts.length,
  ...(report.error ? { error: report.error } : {}),
  report: resolve(artifacts, "report.json"),
}, null, 2));
process.exit(report.status === "pass" ? 0 : 1);
