import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";
import {
  guestQuery,
  primorskayaId,
  languageFindings,
} from "./guest-language-capture.mjs";
import {
  fixtureAssetHost,
  fixtureHotelId,
  noPhotoHotelId,
  photosFixtures,
} from "./photos-fixtures.mjs";

const base = process.env.OKNO_BASE_URL || "http://localhost:8080";
const artifacts = resolve(
  process.env.OKNO_PHOTOS_ARTIFACTS || "../runtime/photos-smoke",
);
const chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const executablePath =
  process.env.OKNO_BROWSER_EXECUTABLE ||
  (existsSync(chrome) ? chrome : undefined);
await mkdir(artifacts, { recursive: true });
const failures = [],
  checks = [],
  captures = [];
const check = (condition, code, detail) => {
  if (!condition) failures.push({ code, detail });
};
async function json(path, body) {
  const response = await fetch(
    `${base}${path}`,
    body
      ? {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(body),
        }
      : undefined,
  );
  assert.ok(response.ok, `${response.status} ${path}`);
  return response.json();
}
const search = await json("/api/search", { text: guestQuery, limit: 40 });
const [detail, catalogue, specs, status, index] = await Promise.all([
  json(`/api/hotels/${primorskayaId}`),
  json("/api/hotels"),
  json("/api/characteristics"),
  json("/api/status"),
  json("/data/seed/hotel_photos.json"),
]);
const fixtures = photosFixtures(detail, search);
const browser = await chromium.launch({ executablePath, headless: true });
const forbiddenHost = (url) =>
  /(?:^|\.)(?:yandex\.[a-z.]+|ya\.ru|mapillary\.com)$/.test(
    new URL(url).hostname,
  );
const expectedSummary = (hit) => {
  const counts = { yes: 0, partial: 0, no: 0, unknown: 0 };
  for (const item of [
    ...hit.verdicts,
    ...(hit.room_plan || []).flatMap((room) => room.verdicts),
  ])
    if (item.required) counts[item.status]++;
  counts.unknown += Math.max(
    0,
    hit.must_total - Object.values(counts).reduce((a, b) => a + b, 0),
  );
  return counts;
};
async function capture(page, name, language = true) {
  const text = await page.locator("body").innerText();
  const findings = languageFindings(text);
  check(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth + 1,
    ),
    "horizontal-overflow",
    name,
  );
  if (language) {
    check(findings.forbiddenPhrases.length === 0, "technical-language", {
      name,
      phrases: findings.forbiddenPhrases,
    });
    // Result lists repeat their parallel card text; inspect each card individually.
    if (!name.includes("search"))
      check(findings.repeatedPhrases.length === 0, "repeated-language", {
        name,
        phrases: findings.repeatedPhrases,
      });
  }
  await page.screenshot({
    path: resolve(artifacts, `${name}.png`),
    fullPage: !name.includes("gallery"),
  });
  await writeFile(resolve(artifacts, `${name}.txt`), text);
  captures.push({ name, ...findings });
}
try {
  // Live API and real catalogue: fixtures below are a separate browser context.
  const live = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
  });
  const page = await live.newPage();
  const requests = [],
    errors = [];
  page.on("request", (request) => requests.push(request.url()));
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`${base}/search?q=${encodeURIComponent(guestQuery)}`);
  await page.locator(".result-card").first().waitFor();
  await page.getByTestId(`hotel-card-${search.results[0].hotel.id}`).waitFor();
  await page
    .locator(".hotel-visual img")
    .first()
    .waitFor({ state: "attached" })
    .catch(() => {});
  const firstTwenty = search.results.slice(0, 20);
  for (const hit of firstTwenty) {
    const card = page.getByTestId(`hotel-card-${hit.hotel.id}`);
    await card.scrollIntoViewIfNeeded();
    await card.locator(".hotel-visual img").evaluateAll((images) =>
      Promise.all(
        images.map((img) =>
          img.complete
            ? Promise.resolve()
            : new Promise((resolve) => {
                img.addEventListener("load", resolve, { once: true });
                img.addEventListener("error", resolve, { once: true });
                setTimeout(resolve, 12000);
              }),
        ),
      ),
    );
    await page.waitForTimeout(40);
    const photoOrLink = await card
      .locator(".hotel-visual")
      .evaluate(
        (node) =>
          !!node.querySelector('[data-testid="hotel-map-photos-link"]') ||
          [...node.querySelectorAll(".has-photo img, img")].some(
            (img) =>
              node.classList.contains("has-photo") &&
              img.complete &&
              img.naturalWidth > 0,
          ),
      );
    check(photoOrLink, "first-20-photo-or-link", hit.hotel.id);
    const summary = await card.locator(".result-summary").innerText();
    const counts = expectedSummary(hit);
    if (counts.partial)
      check(
        summary.includes(`${counts.partial} — пока неточно`),
        "partial-count",
        { id: hit.hotel.id, summary, counts },
      );
    if (counts.yes && counts.yes !== hit.must_total)
      check(summary.includes(`${counts.yes} подтверждено`), "confirmed-count", {
        id: hit.hotel.id,
        summary,
        counts,
      });
    if (counts.no)
      check(summary.includes(`${counts.no} не подходит`), "no-count", {
        id: hit.hotel.id,
        summary,
        counts,
      });
    if (counts.unknown)
      check(
        summary.includes(`по ${counts.unknown} нет данных`),
        "unknown-count",
        { id: hit.hotel.id, summary, counts },
      );
    const findings = languageFindings(await card.innerText());
    check(
      !findings.forbiddenPhrases.length && !findings.repeatedPhrases.length,
      "search-card-language",
      { id: hit.hotel.id, findings },
    );
  }
  const partial = search.results.some((hit) =>
    [
      ...hit.verdicts,
      ...(hit.room_plan || []).flatMap((room) => room.verdicts),
    ].some((v) => v.status === "partial"),
  );
  check(
    (await page.getByTestId("partial-legend").count()) === Number(partial),
    "one-partial-legend",
  );
  const primorHit = search.results.find(
    (hit) => hit.hotel.id === primorskayaId,
  );
  if (primorHit)
    checks.push({
      name: "primorskaya-required-status-counts",
      counts: expectedSummary(primorHit),
    });
  const noPhoto = firstTwenty.find(
    (hit) =>
      !(index.photos[hit.hotel.id] || []).some((photo) =>
        ["building", "official_site", "street"].includes(photo.kind),
      ),
  );
  for (const width of [1440, 390])
    for (const theme of ["light", "dark"]) {
      await page.setViewportSize({ width, height: 1000 });
      await page.evaluate(
        (theme) => localStorage.setItem("okno-theme", theme),
        theme,
      );
      await page.goto(`${base}/search?q=${encodeURIComponent(guestQuery)}`);
      await page.locator(".result-card").first().waitFor();
      await capture(page, `search-${width}-${theme}`);
      await page.goto(`${base}/hotel/${primorskayaId}`);
      await page.getByTestId("room-advice").waitFor();
      await page.getByTestId("hotel-map-photos-link").waitFor();
      await capture(page, `hotel-primorskaya-${width}-${theme}`);
      const roomPhotos = (index.photos[primorskayaId] || []).filter(
        (photo) => photo.kind === "room",
      );
      check(
        (await page.getByTestId("open-room-photos").count()) ===
          Number(roomPhotos.length > 0),
        "real-room-button",
        { count: roomPhotos.length },
      );
      if (noPhoto) {
        await page.goto(`${base}/hotel/${noPhoto.hotel.id}`);
        await page.getByTestId("hotel-map-photos-link").waitFor();
        await capture(page, `hotel-previously-no-photo-${width}-${theme}`);
      }
    }
  const roomUrls = new Set(
    Object.values(index.photos || {})
      .flat()
      .filter((photo) => photo.kind === "room")
      .map((photo) => photo.url),
  );
  check(
    !requests.some((url) => forbiddenHost(url) || roomUrls.has(url)),
    "no-map-or-room-request-before-click",
    requests.filter((url) => forbiddenHost(url) || roomUrls.has(url)),
  );
  check(errors.length === 0, "live-page-errors", errors);
  const advice = await json(`/api/hotels/${primorskayaId}/room-advice?month=9`);
  const quiet = advice.find((item) => item.characteristic_id === "quiet_night");
  const west5 = detail.facts.filter(
    (fact) =>
      fact.characteristic_id === "quiet_night" &&
      fact.scope?.facade === "W" &&
      (fact.scope.floor_min || 1) <= 5 &&
      (fact.scope.floor_max || Infinity) >= 5 &&
      !["unknown", "stale", "disputed"].includes(fact.status) &&
      Number.isFinite(fact.value?.night_db_min) &&
      Number.isFinite(fact.value?.night_db_max),
  );
  if (west5.length)
    check(
      quiet?.options.some(
        (option) =>
          option.scope.facade === "W" &&
          (option.scope.floor_min || 1) <= 5 &&
          (option.scope.floor_max || Infinity) >= 5,
      ),
      "primorskaya-noise-west5-retained",
      {
        west5FactIds: west5.map((fact) => fact.id),
        quietOptions: quiet?.options,
      },
    );
  checks.push(
    { name: "live-first-20-photo-or-link", count: firstTwenty.length },
    { name: "live-no-preclick-map-room-requests", requests: requests.length },
  );
  await live.close();

  // Controlled positive/negative/error cases; the API and images here are fixtures.
  const ctx = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
  });
  let adviceOptions = [fixtures.option(1, 4, "SW")],
    requested = ["quiet_night"],
    brokenRooms = false,
    brokenCover = false;
  const fixtureRequests = [];
  const imageBytes = await readFile(
    new URL("../public/illustrations/passport.webp", import.meta.url),
  );
  await ctx.route("**/*", async (route) => {
    const req = route.request(),
      url = new URL(req.url());
    if (url.hostname === fixtureAssetHost) {
      fixtureRequests.push(url.href);
      if (
        (url.pathname.startsWith("/room-") && brokenRooms) ||
        (url.pathname === "/cover.webp" && brokenCover)
      )
        return route.abort();
      return route.fulfill({ contentType: "image/webp", body: imageBytes });
    }
    if (url.pathname.startsWith("/data/seed/photos/photos-fixture-"))
      return route.fulfill({ contentType: "image/webp", body: imageBytes });
    if (url.pathname === "/data/seed/hotel_photos.json")
      return route.fulfill({ json: { photos: fixtures.photos } });
    if (url.pathname === "/api/status") return route.fulfill({ json: status });
    if (url.pathname === "/api/characteristics")
      return route.fulfill({ json: specs });
    if (url.pathname === "/api/hotels")
      return route.fulfill({
        json: [...catalogue, fixtures.hotel, fixtures.emptyHotel],
      });
    if (url.pathname === "/api/search") {
      const body = req.postDataJSON();
      return route.fulfill({
        json: body.similar_to
          ? { ...fixtures.search, results: [], total: 0 }
          : fixtures.search,
      });
    }
    if (
      url.pathname.startsWith(`/api/hotels/${fixtureHotelId}`) ||
      url.pathname.startsWith(`/api/hotels/${noPhotoHotelId}`)
    ) {
      if (url.pathname.endsWith("/room-advice"))
        return route.fulfill({ json: fixtures.advice(adviceOptions) });
      if (url.pathname.endsWith("/rooms"))
        return route.fulfill({ json: [fixtures.profile] });
      if (url.pathname.endsWith("/geo"))
        return route.fulfill({
          json: {
            center: [fixtures.hotel.lon, fixtures.hotel.lat],
            facades: [],
          },
        });
      return route.fulfill({
        json: {
          ...fixtures.detail,
          hotel: url.pathname.includes(noPhotoHotelId)
            ? fixtures.emptyHotel
            : fixtures.hotel,
        },
      });
    }
    return route.continue();
  });
  await ctx.addInitScript(
    ({ parsed, requested }) => {
      parsed.must = parsed.must.filter((criterion) =>
        requested.includes(criterion.characteristic_id),
      );
      sessionStorage.setItem("okno-parsed", JSON.stringify(parsed));
    },
    { parsed: fixtures.parsed, requested },
  );
  const fpage = await ctx.newPage();
  fpage.on("request", (request) => {
    if (forbiddenHost(request.url())) fixtureRequests.push(request.url());
  });
  const loadHotel = async () => {
    await fpage.goto(`${base}/hotel/${fixtureHotelId}`);
    await fpage.getByTestId("open-room-photos").waitFor();
    await fpage.getByTestId("room-advice-card").waitFor();
  };
  await loadHotel();
  check(
    (await fpage.getByTestId("open-room-photos").innerText()).includes("(10)"),
    "only-safe-room-kind-count",
  );
  check(
    !fixtureRequests.some((url) => /\/room-/.test(url) || forbiddenHost(url)),
    "fixture-room-and-map-lazy",
    fixtureRequests,
  );
  check(
    (await fpage.getByTestId("room-advice-compromise").count()) === 1,
    "requested-incompatible-scope-compromise",
  );
  check(
    (await fpage.getByTestId("room-advice-compromise").innerText()).includes(
      "юго-запад · этажи 1–4",
    ),
    "compromise-uses-option",
  );
  check(
    !(await fpage.getByTestId("room-advice-compromise").innerText()).includes(
      "нет заката",
    ),
    "no-invented-negative",
  );
  adviceOptions = [fixtures.option(1, 4), fixtures.option(5, 6)];
  await loadHotel();
  check(
    (await fpage.getByTestId("room-advice-compromise").count()) === 0,
    "adjacent-options-cover-recommended-scope",
  );
  adviceOptions = [fixtures.option(1, 4)];
  await loadHotel();
  check(
    (await fpage.getByTestId("room-advice-compromise").count()) === 1,
    "overlap-is-not-full-scope-coverage",
  );
  fixtures.parsed.must = fixtures.parsed.must.filter(
    (criterion) => criterion.characteristic_id !== "quiet_night",
  );
  await fpage.goto(`${base}/search?q=fixture`);
  await fpage
    .getByTestId(`hotel-card-${fixtureHotelId}`)
    .locator(".hotel-name")
    .click();
  await fpage.getByTestId("room-advice-card").waitFor();
  check(
    (await fpage.getByTestId("room-advice-compromise").count()) === 0,
    "unrequested-goal-has-no-compromise",
  );
  for (const width of [1440, 390])
    for (const theme of ["light", "dark"]) {
      await fpage.setViewportSize({ width, height: 1000 });
      await fpage.evaluate(
        (theme) => localStorage.setItem("okno-theme", theme),
        theme,
      );
      await loadHotel();
      await fpage.getByTestId("open-room-photos").click();
      const gallery = fpage.getByTestId("room-photo-gallery");
      await gallery.waitFor();
      check((await gallery.locator("img").count()) === 8, "gallery-max-eight");
      check(
        (await gallery.innerText()).includes(
          "С сайта отеля · ваш номер может отличаться",
        ),
        "gallery-caption",
      );
      check(
        (await gallery
          .getByTestId("room-photos-source")
          .getAttribute("href")) === `https://${fixtureAssetHost}/rooms`,
        "gallery-source",
      );
      await capture(fpage, `gallery-fixture-${width}-${theme}`, false);
      await fpage.keyboard.press("Escape");
      check((await gallery.count()) === 0, "escape-closes-gallery");
      check(
        await fpage
          .getByTestId("open-room-photos")
          .evaluate((node) => node === document.activeElement),
        "gallery-focus-restored",
      );
    }
  brokenRooms = true;
  await loadHotel();
  await fpage.getByTestId("open-room-photos").click();
  for (
    let n = 0;
    n < 10 &&
    (await fpage.getByTestId("room-photo-gallery").locator("img").count());
    n++
  ) {
    await fpage
      .getByTestId("room-photo-gallery")
      .locator("img")
      .first()
      .scrollIntoViewIfNeeded()
      .catch(() => {});
    await fpage.waitForTimeout(100);
  }
  check(
    (await fpage
      .getByTestId("room-photo-gallery")
      .getByRole("status")
      .count()) === 1,
    "all-room-errors-have-message",
  );
  check(
    (await fpage.getByTestId("room-photos-source").count()) === 1,
    "all-room-errors-retain-source",
  );
  await fpage.keyboard.press("Escape");
  brokenRooms = false;
  await fpage.getByTestId("open-room-photos").click();
  check(
    (await fpage.getByTestId("room-photo-gallery").locator("img").count()) ===
      8,
    "gallery-errors-reset-on-reopen",
  );
  // A client-side hotel change must unmount the old open gallery and its errors.
  const spaHotel = async (id) => {
    await fpage.evaluate((path) => {
      history.pushState({}, "", path);
      dispatchEvent(new PopStateEvent("popstate"));
    }, `/hotel/${id}`);
    await fpage
      .locator("h1")
      .filter({
        hasText:
          id === fixtureHotelId
            ? fixtures.hotel.name
            : fixtures.emptyHotel.name,
      })
      .waitFor();
  };
  await spaHotel(noPhotoHotelId);
  check(
    (await fpage.getByTestId("room-photo-gallery").count()) === 0,
    "hotel-change-closes-old-gallery",
  );
  await fpage.getByTestId("hotel-map-photos-link").waitFor();
  check(
    (await fpage.getByTestId("open-room-photos").count()) === 0,
    "room-button-absent-without-rooms",
  );
  await spaHotel(fixtureHotelId);
  await fpage.getByTestId("open-room-photos").click();
  check(
    (await fpage.getByTestId("room-photo-gallery").locator("img").count()) ===
      8,
    "hotel-change-resets-gallery-errors",
  );
  await fpage.keyboard.press("Escape");
  // Deliberately shuffled input proves that room/street never outrank the building.
  const originalPhotos = fixtures.photos[fixtureHotelId];
  const building = {
    kind: "building",
    local: true,
    url: "/data/seed/photos/photos-fixture-building.webp",
    page_url: "https://commons.wikimedia.org/wiki/File:Fixture.webp",
    author: "Автор Commons",
    license: "CC BY-SA 4.0",
  };
  const street = {
    kind: "street",
    local: true,
    url: "/data/seed/photos/photos-fixture-street.webp",
    page_url: "https://www.mapillary.com/app/?pKey=fixture",
    author: "Автор проверки",
    captured_year: 2024,
  };
  fixtures.photos[fixtureHotelId] = [
    street,
    originalPhotos[1],
    originalPhotos[0],
    building,
  ];
  await loadHotel();
  check(
    (await fpage.locator(".detail-visual > img").getAttribute("src")) ===
      building.url,
    "building-before-official-and-street",
  );
  fixtures.photos[fixtureHotelId] = fixtures.photos[fixtureHotelId].filter(
    (photo) => photo.kind !== "building",
  );
  await loadHotel();
  check(
    (await fpage.locator(".detail-visual > img").getAttribute("src")) ===
      originalPhotos[0].url,
    "official-before-street",
  );
  brokenCover = true;
  await loadHotel();
  await fpage.locator(`.detail-visual > img[src="${street.url}"]`).waitFor();
  check(
    (await fpage.locator(".detail-visual .photo-credit").innerText()).includes(
      "Вид с улицы · Mapillary · Автор проверки, 2024",
    ),
    "street-caption-author-year",
  );
  fixtures.photos[fixtureHotelId] = originalPhotos;
  await fpage.goto(`${base}/search?q=fixture`);
  const fcard = fpage.getByTestId(`hotel-card-${fixtureHotelId}`);
  await fcard.getByTestId("hotel-map-photos-link").waitFor();
  check(
    (await fcard.locator(".result-summary").innerText()).includes(
      "1 подтверждено, 2 — пока неточно",
    ),
    "fixture-honest-count",
  );
  check(
    (await fpage.getByTestId("partial-legend").count()) === 1,
    "fixture-one-legend",
  );
  const second = await fpage
    .getByTestId(`hotel-card-${noPhotoHotelId}`)
    .locator(".result-summary")
    .innerText();
  check(
    second.includes("1 — пока неточно") &&
      second.includes("1 не подходит") &&
      second.includes("по 3 нет данных"),
    "room-and-no-data-counts",
    second,
  );
  check(
    !(await fpage.locator(".result-group-title").first().innerText()).includes(
      "УСЛОВИЯ СОШЛИСЬ",
    ),
    "partial-group-heading-honest",
  );
  const mapLink = fcard.getByTestId("hotel-map-photos-link");
  check(
    (await mapLink.getAttribute("target")) === "_blank" &&
      (await mapLink.getAttribute("rel")).includes("noopener"),
    "external-link-safe",
  );
  const mapQuery = new URL(await mapLink.getAttribute("href")).searchParams.get(
    "text",
  );
  check(
    [
      fixtures.hotel.name,
      String(fixtures.hotel.lat),
      String(fixtures.hotel.lon),
    ].every((part) => mapQuery.includes(part)),
    "map-link-name-and-coordinates",
  );
  check(
    !fixtureRequests.some(forbiddenHost),
    "fixture-no-map-requests",
    fixtureRequests.filter(forbiddenHost),
  );
  checks.push({
    name: "isolated-fixtures",
    cases: [
      "safe URLs",
      "gallery lazy/max8/error/reset/Escape",
      "scope coverage/requested-only",
      "search fallback/group/no_data",
    ],
  });
  await ctx.close();
} finally {
  await browser.close();
}
const result = {
  status: failures.length ? "failed" : "passed",
  base,
  artifacts,
  checks,
  captures,
  failures,
};
await writeFile(
  resolve(artifacts, "photos-smoke-result.json"),
  JSON.stringify(result, null, 2),
);
console.log(
  JSON.stringify(
    { ...result, captures: captures.map(({ name }) => name) },
    null,
    2,
  ),
);
if (failures.length) process.exitCode = 1;
