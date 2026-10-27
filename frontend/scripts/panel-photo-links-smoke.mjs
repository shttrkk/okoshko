import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";

const base = process.env.OKNO_BASE_URL || "http://localhost:8080";
const artifacts = resolve(
  process.env.OKNO_PHOTO_LINK_ARTIFACTS || "../runtime/panel-photo-links",
);
const query = "тихий номер с видом на закат недалеко от моря";
const brevisId = "osm-way-201474330";
const primorskayaId = "overture-037ee676-5776-46bd-a4ac-38c828f2a99d";
const chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const report = {
  base,
  query,
  run_id: null,
  checks: [],
  screenshots: [],
  blocked_providers: [],
  intercepted_posts: [],
};
const check = (condition, name) => {
  assert.ok(condition, name);
  report.checks.push(name);
};
const provider = (url) =>
  /(?:^|\.)(?:yandex\.[a-z.]+|ya\.ru|mapillary\.com)$/.test(
    new URL(url).hostname,
  );
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
await mkdir(artifacts, { recursive: true });
// This single authorized search records a normal trace. Browser replays below
// intercept every POST, including search; no questions or passports are written.
const savedSearch = process.env.OKNO_PHOTO_LINK_SEARCH_FIXTURE;
const search = savedSearch
  ? JSON.parse(await readFile(savedSearch, "utf8"))
  : await json("/api/search", { text: query, limit: 40 });
await writeFile(
  resolve(artifacts, "search-result.json"),
  JSON.stringify(search),
);
report.run_id = search.run_id;
check(
  search.results.length === 40,
  "real Masha search has its first 40 results",
);
const [catalogue, specs, status, index, brevis, primorskaya] =
  await Promise.all([
    json("/api/hotels"),
    json("/api/characteristics"),
    json("/api/status"),
    json("/data/seed/hotel_photos.json"),
    json(`/api/hotels/${brevisId}?compact=true`),
    json(`/api/hotels/${primorskayaId}?compact=true`),
  ]);
check(
  /Орджоникидзе.*11\/1/u.test(brevis.hotel.address),
  "Brevis address comes from hotel data",
);
const roomUrls = new Set(
  Object.values(index.photos)
    .flat()
    .filter((p) => p.kind === "room")
    .map((p) => p.url),
);
const withoutAddress = {
  ...brevis.hotel,
  id: "photo-links-no-address",
  name: "Проверка без адреса",
  address: "Сочи",
  image: null,
  tags: {},
};
const areaAddress = {
  ...withoutAddress,
  id: "photo-links-area",
  name: "Проверка области",
  address: brevis.hotel.address,
  tags: { address_precision: "area" },
};
const brokenCover = {
  ...withoutAddress,
  id: "photo-links-broken",
  name: "Проверка недоступного фото",
};
const fixtureHotels = [withoutAddress, areaAddress, brokenCover];
const roomPhoto = {
  kind: "room",
  url: "/data/seed/photos/panel-links-room.webp",
  local: true,
  page_url: "https://photo-links.example/rooms",
};
const fixtureIndex = {
  photos: {
    [withoutAddress.id]: [roomPhoto], // Room interiors must never count as a cover.
    [areaAddress.id]: [],
    [brokenCover.id]: [
      {
        kind: "building",
        url: "/data/seed/photos/panel-links-broken.webp",
        local: true,
      },
      roomPhoto,
    ],
  },
};
const fixtureDetail = (hotel) => ({
  ...brevis,
  hotel,
  facts: [],
  claims: [],
  conflicts: [],
  verdicts: [],
  room_plan: [],
  room_profile: null,
  room_recommendation: "",
  disappointments: [],
});
const fixtureSearch = {
  ...search,
  results: [{ ...search.results[0], hotel: brokenCover }],
  total: 1,
};
const browser = await chromium.launch({
  headless: true,
  executablePath:
    process.env.OKNO_BROWSER_EXECUTABLE ||
    (existsSync(chrome) ? chrome : undefined),
});

async function capture(page, name, locator) {
  const path = resolve(artifacts, `${name}.png`);
  if (locator) {
    await locator.scrollIntoViewIfNeeded();
    await locator.locator("img").evaluateAll((images) =>
      Promise.all(
        images.map((img) =>
          img.complete
            ? undefined
            : new Promise((resolve) => {
                img.addEventListener("load", resolve, { once: true });
                img.addEventListener("error", resolve, { once: true });
                setTimeout(resolve, 8000);
              }),
        ),
      ),
    );
    await locator.screenshot({ path });
  } else await page.screenshot({ path });
  report.screenshots.push(path);
}
async function checkLink(link, hotel, name, prominent) {
  await link.waitFor();
  const href = new URL(await link.getAttribute("href"));
  check(
    href.origin === "https://yandex.ru" && href.pathname === "/maps/",
    `${name}: exact provider path`,
  );
  check(href.searchParams.get("text") === hotel.name, `${name}: name in URL`);
  for (const field of ["ll", "sll"])
    check(
      href.searchParams.get(field) === `${hotel.lon},${hotel.lat}`,
      `${name}: ${field} preserves longitude/latitude`,
    );
  check(href.searchParams.get("z") === "17", `${name}: zoom 17`);
  check(
    (await link.getAttribute("target")) === "_blank",
    `${name}: opens a new tab`,
  );
  check(
    (await link.getAttribute("rel")).split(/\s+/).sort().join(" ") ===
      "noopener noreferrer",
    `${name}: safe rel`,
  );
  check(
    (await link.getAttribute("aria-label")) ===
      `Фото и отзывы о «${hotel.name}» на Яндекс Картах, откроется в новой вкладке`,
    `${name}: exact accessible name`,
  );
  check(
    (await link.innerText()) === "Фото и отзывы на Яндекс Картах ↗",
    `${name}: visible label`,
  );
  if (prominent) {
    check(
      (await link.boundingBox()).height >= 44,
      `${name}: touch target at least 44px`,
    );
    check(
      await link
        .locator("..")
        .getByText("Откроется в новой вкладке", { exact: true })
        .isVisible(),
      `${name}: visible new-tab caption`,
    );
    const contrast = await link.evaluate((node) => {
      const style = getComputedStyle(node);
      const luminance = (color) => {
        const channels = color
          .match(/[\d.]+/g)
          .slice(0, 3)
          .map(Number)
          .map((c) => c / 255)
          .map((c) =>
            c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4,
          );
        return (
          channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722
        );
      };
      const a = luminance(style.color),
        b = luminance(style.backgroundColor);
      return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
    });
    check(contrast >= 4.5, `${name}: button text contrast at least 4.5`);
  }
}
async function clickExternal(page, link, parent, name) {
  await parent.evaluate((node) => {
    window.photoLinkBubbles = 0;
    node.addEventListener("click", () => window.photoLinkBubbles++);
  });
  const before = new URL(page.url()).pathname;
  // Context routing is installed before navigation. The popup is captured and
  // closed even though its provider request is aborted: no provider page opens.
  const [popup] = await Promise.all([page.waitForEvent("popup"), link.click()]);
  await popup.close();
  check(
    new URL(page.url()).pathname === before,
    `${name}: local pathname unchanged after click`,
  );
  check(
    (await page.evaluate(() => window.photoLinkBubbles)) === 0,
    `${name}: click does not bubble into hotel card`,
  );
}
async function contextFor(width, theme, fixtures) {
  const context = await browser.newContext({
    viewport: { width, height: 1000 },
    colorScheme: theme,
    locale: "ru-RU",
  });
  const requests = [],
    errors = [];
  await context.addInitScript(
    ({ theme, origin }) => {
      if (location.origin === origin) localStorage.setItem("okno-theme", theme);
    },
    { theme, origin: new URL(base).origin },
  );
  context.on("request", (request) => requests.push(request.url()));
  context.on("page", (page) =>
    page.on("pageerror", (error) => errors.push(error.message)),
  );
  await context.route("**/*", async (route) => {
    const request = route.request(),
      url = new URL(request.url());
    if (provider(url.href)) {
      report.blocked_providers.push(url.href);
      return route.abort();
    }
    if (
      url.hostname === "photo-links.example" ||
      url.pathname.startsWith("/data/seed/photos/panel-links-")
    )
      return route.abort();
    if (fixtures && url.pathname === "/data/seed/hotel_photos.json")
      return route.fulfill({ json: fixtureIndex });
    if (fixtures && url.pathname === "/api/hotels")
      return route.fulfill({ json: [...catalogue, ...fixtureHotels] });
    if (fixtures && url.pathname === "/api/characteristics")
      return route.fulfill({ json: specs });
    if (fixtures && url.pathname === "/api/status")
      return route.fulfill({ json: status });
    const fixture =
      fixtures &&
      fixtureHotels.find((hotel) =>
        url.pathname.startsWith(`/api/hotels/${hotel.id}`),
      );
    if (
      url.pathname.startsWith("/api/") &&
      !["GET", "HEAD", "OPTIONS"].includes(request.method())
    ) {
      report.intercepted_posts.push(url.pathname);
      if (url.pathname === "/api/search") {
        const body = request.postDataJSON();
        return route.fulfill({
          json: body.similar_to
            ? { ...search, results: [], total: 0 }
            : fixtures
              ? fixtureSearch
              : search,
        });
      }
      if (fixture && url.pathname.endsWith("/evaluate"))
        return route.fulfill({ json: fixtureDetail(fixture) });
      errors.push(`Unexpected mutation blocked: ${url.pathname}`);
      return route.fulfill({
        status: 409,
        json: { detail: "Smoke blocks state changes" },
      });
    }
    if (fixture) {
      if (url.pathname.endsWith("/geo"))
        return route.fulfill({
          json: { center: [fixture.lon, fixture.lat], facades: [] },
        });
      if (
        url.pathname.endsWith("/room-advice") ||
        url.pathname.endsWith("/rooms")
      )
        return route.fulfill({ json: [] });
      return route.fulfill({ json: fixtureDetail(fixture) });
    }
    return route.continue();
  });
  return { context, page: await context.newPage(), requests, errors };
}
function noAutomaticProviders(requests, name) {
  check(!requests.some(provider), `${name}: no pre-click provider requests`);
  check(
    !requests.some(
      (url) => roomUrls.has(url) || url.includes("panel-links-room.webp"),
    ),
    `${name}: room gallery images stay lazy`,
  );
}
try {
  for (const width of [1440, 390])
    for (const theme of ["light", "dark"]) {
      const name = `${width}-${theme}`;
      const { context, page, requests, errors } = await contextFor(
        width,
        theme,
        false,
      );
      await page.goto(`${base}/search?q=${encodeURIComponent(query)}`);
      await page.locator(".result-card").first().waitFor();
      await page.waitForFunction(
        () => document.querySelectorAll(".result-card").length === 40,
      );
      let firstFallback;
      for (const hit of search.results) {
        const card = page.getByTestId(`hotel-card-${hit.hotel.id}`);
        const visual = card.locator(".hotel-visual");
        if (
          !(index.photos[hit.hotel.id] || []).some(
            (photo) => photo.kind !== "room",
          ) &&
          !hit.hotel.image?.startsWith("/data/")
        ) {
          const link = card.getByTestId("hotel-map-photos-link");
          await checkLink(link, hit.hotel, `${name}: ${hit.hotel.id}`, true);
          firstFallback ||= { hit, card, link };
        } else
          check(
            (await visual.count()) === 1,
            `${name}: cover card retains hotel visual`,
          );
      }
      check(!!firstFallback, `${name}: real search includes a fallback button`);
      noAutomaticProviders(requests, `${name}: search`);
      check(
        (await page
          .locator(
            'iframe[src*="yandex"], link[rel*="prefetch"][href*="yandex"]',
          )
          .count()) === 0,
        `${name}: no embedded or prefetched provider`,
      );
      await page.evaluate(() => scrollTo(0, 0));
      await capture(page, `search-${name}`);
      await capture(page, `no-cover-card-${name}`, firstFallback.card);
      await clickExternal(
        page,
        firstFallback.link,
        firstFallback.card,
        `${name}: search button`,
      );
      for (const [id, detail, covered] of [
        [brevisId, brevis, false],
        [primorskayaId, primorskaya, true],
      ]) {
        await page.evaluate(() => sessionStorage.removeItem("okno-parsed"));
        const before = requests.length;
        await page.goto(`${base}/hotel/${id}`);
        const actions = page.locator(".hotel-photo-actions");
        if (covered) {
          await page.locator(".detail-visual.has-photo").waitFor();
          await actions
            .locator(".photo-map-callout-detail")
            .waitFor({ state: "detached" });
        }
        const link = actions.getByTestId("hotel-map-photos-link");
        await checkLink(
          link,
          detail.hotel,
          `${name}: ${detail.hotel.name}`,
          !covered,
        );
        if (!covered)
          check(
            (await actions.innerText()).includes(
              `На Картах отель может называться иначе — сверьте адрес: ${brevis.hotel.address}`,
            ),
            `${name}: exact Brevis address hint`,
          );
        else
          check(
            (await actions.locator(".photo-map-callout-detail").count()) === 0,
            `${name}: real cover keeps secondary link`,
          );
        noAutomaticProviders(requests.slice(before), `${name}: ${id}`);
        await capture(
          page,
          `hotel-${id}-${name}`,
          page.locator(".detail-visual-wrap"),
        );
      }
      check(
        errors.length === 0,
        `${name}: no browser exceptions or unexpected API writes: ${JSON.stringify(errors)}`,
      );
      await context.close();

      const fixture = await contextFor(width, theme, true);
      for (const hotel of [withoutAddress, areaAddress]) {
        await fixture.page.goto(`${base}/hotel/${hotel.id}`);
        const actions = fixture.page.locator(".hotel-photo-actions");
        await checkLink(
          actions.getByTestId("hotel-map-photos-link"),
          hotel,
          `${name}: ${hotel.id}`,
          true,
        );
        check(
          (await actions.innerText()).includes(
            "На Картах отель может называться иначе — сверьте место на карте",
          ),
          `${name}: ${hotel.id} uses location hint`,
        );
        check(
          !(await actions.innerText()).includes("сверьте адрес:"),
          `${name}: ${hotel.id} never promises a street address`,
        );
        noAutomaticProviders(fixture.requests, `${name}: ${hotel.id}`);
        if (hotel.id === withoutAddress.id) {
          await actions.getByTestId("open-room-photos").waitFor();
          check(
            (await actions.getByTestId("open-room-photos").count()) === 1,
            `${name}: room-only index keeps gallery but no cover`,
          );
          await capture(
            fixture.page,
            `no-address-${name}`,
            fixture.page.locator(".detail-visual-wrap"),
          );
        }
      }
      const failedCoverRequest = fixture.page.waitForRequest((request) =>
        request.url().includes("panel-links-broken.webp"),
      );
      await fixture.page.goto(`${base}/search?q=photo-error-fixture`);
      await failedCoverRequest;
      const broken = fixture.page.getByTestId(`hotel-card-${brokenCover.id}`);
      await broken.locator(".hotel-visual:not(.has-photo)").waitFor();
      await checkLink(
        broken.getByTestId("hotel-map-photos-link"),
        brokenCover,
        `${name}: broken cover fallback`,
        true,
      );
      await capture(fixture.page, `broken-cover-${name}`, broken);
      check(
        fixture.errors.length === 0,
        `${name}: fixture pages have no browser exceptions`,
      );
      check(
        await fixture.page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth + 1,
        ),
        `${name}: no horizontal overflow`,
      );
      await fixture.context.close();
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
  `PASS: ${report.checks.length} checks; search run ${report.run_id}; artifacts ${artifacts}`,
);
