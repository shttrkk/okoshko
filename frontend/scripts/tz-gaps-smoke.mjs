import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { closeSmokeBrowser } from "./panel-guest-smoke-ui.mjs";

// Known-answer fixtures are synthetic and exist only in this browser context.
// Optional live mode reads the isolated :8081 API. No questions/answers/passports are written.
const base = process.env.OKNO_BASE_URL || "http://localhost:5190";
const apiBase = process.env.OKNO_API_BASE_URL || "http://localhost:8081";
assert.equal(
  new URL(apiBase).port,
  "8081",
  "Live API must use isolated port 8081",
);
for (const url of [base, apiBase])
  assert.notEqual(
    new URL(url).port,
    "8080",
    "Protected demo port is forbidden",
  );
const live = process.env.OKNO_TZ_LIVE === "1";
const artifacts = resolve(
  process.env.OKNO_TZ_ARTIFACTS ||
    fileURLToPath(
      new URL("../../runtime/docsreviews/tz-interface", import.meta.url),
    ),
);
await mkdir(artifacts, { recursive: true });
const browser = await chromium.launch({
  headless: true,
  executablePath:
    process.env.OKNO_BROWSER_EXECUTABLE ||
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
});
const checks = [],
  errors = [],
  mutations = [],
  geoReads = [],
  evidenceReads = [];
const ids = ["tz-fixture-a", "tz-fixture-b"];
const names = {
  airport_noise: "Самолёты рядом",
  airport_transfer: "Дорога из аэропорта",
  car_arrival: "Подъезд на машине",
  climate_days: "Солнце, дожди и купание",
  construction_nearby: "Стройки рядом",
  dog_walk: "Где гулять с собакой",
  landmark_view: "Вид на достопримечательность",
  mobile_masts: "Мачты связи на карте",
  night_open: "Магазины и еда ночью",
  veterinary_nearby: "Ветклиника рядом",
  pet_shop_nearby: "Зоомагазин рядом",
  location_access: "Центр и транспорт",
  mobile_internet: "Мобильный интернет в районе",
  photo_features: "Признаки здания по источникам",
  events_nearby: "Мероприятия рядом",
  hotel_parking: "Собственная парковка",
  family_nearby: "Для семьи",
  car_access: "На машине",
  building_history: "История здания",
  climate: "Климат",
};
const specs = Object.entries(names).map(([id, name_ru]) => ({
  id,
  name_ru,
  display_name: name_ru,
  category: "Окружение",
  scope: id === "landmark_view" ? "floor_range" : "hotel",
  hotelier_answerable: id === "hotel_parking",
}));
const hotels = ids.map((id, i) => ({
  id,
  name: `Учебный отель ${i + 1} · проверочный набор`,
  lat: 43.58,
  lon: 39.72 + i * 0.001,
  address: "Сочи · вымышленные значения для проверки интерфейса",
  stars: null,
  price_rub: null,
  basic_fields_source: "fixture",
  source_ids: [],
  floors: 5,
  footprint: null,
  image: null,
  image_attribution: null,
  website: null,
  room_types: [],
  tags: {},
}));
const walk = (minutes, available = true) => ({
  available,
  minutes,
  distance_m: minutes * 70,
  ascent_m: 7,
  has_steps: false,
  crossings: [{ controlled: true, kind: "major_road" }],
  name: "Учебная точка",
  threshold_minutes: 15,
});
const fact = (hotel, id, value, suffix = "", scope = { type: "hotel" }) => ({
  id: `${hotel.id}:${id}:${suffix}`,
  hotel_id: hotel.id,
  characteristic_id: id,
  value,
  scope,
  confidence: 0.64,
  status: "computed",
  supporting_claim_ids: [`claim:${hotel.id}:${id}:${suffix}`],
  evidence: [],
  updated_at: "2026-10-27T12:00:00Z",
  valid_until: "2027-01-27T12:00:00Z",
  synthetic: true,
});
const allFacts = hotels.map((hotel, i) => [
  fact(hotel, "airport_noise", {
    available: i === 0,
    in_corridor: i === 0,
    runway_distance_m: i ? 5000 : 1400,
  }),
  fact(hotel, "airport_transfer", {
    available: null,
    transfers: i ? null : 0,
    car_distance_km: 5.1,
    scheduled_minutes: null,
    night_service: null,
    primary_unknown_reason: "no_gtfs",
  }),
  fact(hotel, "car_arrival", {
    available: false,
    road_distance_m: 48,
    threshold_m: 30,
    private_parking: null,
  }),
  fact(
    hotel,
    "climate_days",
    {
      sunny_days: 20.2,
      rainy_days: 8.8,
      water_c: 22.4,
      water_comfort: "обычно купаются",
      complete_sun_years: 10,
      complete_rain_years: 10,
    },
    "",
    { type: "hotel", month: 9 },
  ),
  fact(hotel, "construction_nearby", {
    available: null,
    current_eligible_count: 0,
    mapped_count: 206,
  }),
  fact(hotel, "dog_walk", {
    available: null,
    dog_park: null,
    ordinary_park_fallback: walk(7),
  }),
  fact(
    hotel,
    "landmark_view",
    {
      available: true,
      landmark_id: "sea_terminal",
      name: "Морской вокзал — верх крыши без шпиля",
      distance_m: 700,
    },
    "terminal",
    {
      type: "floor_range",
      facade: "W",
      floor_min: 3,
      floor_max: 5,
      landmark_id: "sea_terminal",
    },
  ),
  fact(
    hotel,
    "landmark_view",
    {
      available: null,
      landmark_id: "fisht",
      name: "Фишт",
      unknown_reason: "Нет подтверждённой высоты; видимость не рассчитана",
    },
    "fisht",
    { type: "hotel", landmark_id: "fisht" },
  ),
  fact(hotel, "mobile_masts", {
    mapped_count: 0,
    radius_m: 1000,
    quality_unknown: true,
  }),
  fact(hotel, "night_open", {
    available: true,
    shop: walk(4),
    food: null,
    open_at: "02:00",
  }),
  fact(hotel, "veterinary_nearby", walk(22, false)),
  fact(hotel, "pet_shop_nearby", { available: null, minutes: null }),
  fact(hotel, "location_access", {
    in_central_sochi: false,
    district_name: "Адлерский район",
    zone_center: { name: "Адлер" },
    center_route: walk(12),
    station_route: walk(22, false),
    bus_stop_route: walk(3),
    metro: { available: false, observed_at: "2024-12-31" },
  }),
  fact(
    hotel,
    "mobile_internet",
    i
      ? {
          available: null,
          unknown_reason: "No exact containing mobile tile",
          hotel_wifi: false,
        }
      : {
          available: true,
          download_mbps: 46,
          upload_mbps: 12,
          latency_ms: 34,
          tests: 53,
          devices: 12,
          quarter: "2026Q3",
          radio_generation: null,
          hotel_wifi: false,
        },
  ),
  fact(
    hotel,
    "photo_features",
    i
      ? { available: true, balcony: false, terrace: true, pool: null }
      : {
          available: null,
          balcony: null,
          terrace: null,
          pool: null,
          evidence_records: [],
          unknown_reason: "No exact-linked licensed metadata",
        },
  ),
  fact(hotel, "events_nearby", {
    available: null,
    event_count: null,
    events: [],
    unknown_reason: "No open calendar",
  }),
  fact(hotel, "hotel_parking", {
    private_parking: i ? false : true,
    fee_rub_per_day: i ? null : 500,
    reservation_required: true,
    ev_charging: null,
  }),
  {
    ...fact(hotel, "family_nearby", {
      available: true,
      playground_distance_m: 170,
    }),
    confidence: 0.95,
  },
  fact(
    hotel,
    "family_nearby",
    {
      walk_playground: walk(6),
      walk_hospital: null,
      walk_children_hospital: walk(23, false),
      walk_promenade: walk(4),
    },
    "routes",
  ),
  fact(hotel, "car_access", { available: null, parking_distance_m: 80 }),
  fact(hotel, "car_access", { walk_parking: walk(8), walk_ev: null }, "routes"),
  fact(
    hotel,
    "building_history",
    i
      ? null
      : {
          history_information: true,
          historic_designation: null,
          inception: "2010",
          inception_tag: "start_date",
          explicit_building_date: null,
        },
  ),
  fact(hotel, "climate", { air_c: 25, water_c: 23 }, "baseline", {
    type: "hotel",
    month: 9,
  }),
  fact(
    hotel,
    "climate",
    {
      sunny_days: 20.2,
      rainy_days: 8.8,
      complete_sun_years: 10,
      complete_rain_years: 10,
    },
    "days",
    { type: "hotel", month: 9 },
  ),
]);
const geo = {
  center: [39.72, 43.58],
  floors: { count: 5, modeled_count: 5 },
  facades: [
    {
      code: "W",
      azimuth_deg: 270,
      geometry: {
        type: "LineString",
        coordinates: [
          [39.7199, 43.5799],
          [39.7199, 43.5801],
        ],
      },
    },
  ],
  view_cones: { type: "FeatureCollection", features: [] },
  landmark_targets: {
    type: "FeatureCollection",
    features: [
      {
        type: "Feature",
        id: "sea_terminal",
        geometry: { type: "Point", coordinates: [39.721, 43.581] },
        properties: {
          landmark_id: "sea_terminal",
          name: "Учебный Морской вокзал",
          height_source: "osm_building_levels_3m_and_copernicus_dsm",
          height_m: 12,
        },
      },
      {
        type: "Feature",
        id: "fisht",
        geometry: {
          type: "Polygon",
          coordinates: [
            [
              [39.722, 43.58],
              [39.723, 43.58],
              [39.723, 43.581],
              [39.722, 43.58],
            ],
          ],
        },
        properties: {
          landmark_id: "fisht",
          name: "Учебный Фишт",
          height_source: null,
        },
      },
    ],
  },
  landmark_rays: { type: "FeatureCollection", features: [] },
  landmark_metadata: [
    { landmark_id: "fisht", height_available: false, available: null },
  ],
  limitations: ["Учебная геометрия, не сведения о реальном отеле"],
};
const details = hotels.map((hotel, i) => ({
  hotel,
  facts: allFacts[i],
  claims: [],
  verdicts: [],
  conflicts: [],
  disappointments: [],
  room_recommendation: "Проверочный набор: реальные номера не описывает",
  geo: {},
  room_plan: [],
}));
const parsed = {
  text: "Сначала мобильный интернет, затем магазины ночью",
  must: [
    {
      characteristic_id: "mobile_internet",
      label: names.mobile_internet,
      op: "gte",
      field: "download_mbps",
      value: 20,
      weight: 1,
    },
  ],
  nice: [
    {
      characteristic_id: "night_open",
      label: names.night_open,
      op: "eq",
      value: true,
      weight: 1,
    },
  ],
  month: 9,
  no_data: [],
  no_data_must: [],
  unmapped: [],
  clarifications: [],
  rooms: [],
};
async function context(viewport, colorScheme, fixture) {
  const ctx = await browser.newContext({ viewport, colorScheme });
  await ctx.route(/yandex\.|ya\.ru|mapillary/i, (route) => route.abort());
  await ctx.route("**/api/**", async (route) => {
    const req = route.request(),
      path = new URL(req.url()).pathname;
    if (
      req.method() !== "GET" &&
      !["/api/search", "/api/compare"].includes(path) &&
      !/\/evaluate$/.test(path)
    ) {
      mutations.push(`${req.method()} ${path}`);
      return route.abort();
    }
    if (!fixture) return route.continue();
    let json = [];
    if (path === "/api/characteristics") json = specs;
    else if (path === "/api/hotels") json = hotels;
    else if (path === "/api/status")
      json = {
        mode: "offline",
        hotel_count: 2,
        characteristic_count: specs.length,
      };
    else if (path === "/api/revision")
      json = { instance: "tz-fixture", revision: 1 };
    else if (path === "/api/compare")
      json = { hotels: details, characteristics: specs };
    else if (path === "/api/search") json = { results: [], parsed, total: 0 };
    else {
      const i = hotels.findIndex((hotel) =>
        path.includes(`/hotels/${hotel.id}`),
      );
      if (i >= 0) {
        if (path.endsWith("/geo")) {
          const params = new URL(req.url()).searchParams;
          geoReads.push(Object.fromEntries(params));
          const floor = Number(params.get("floor"));
          json = {
            ...geo,
            landmark_rays: {
              type: "FeatureCollection",
              features:
                floor === 3 && params.get("facade") === "W"
                  ? [
                      {
                        type: "Feature",
                        id: "saved-fixture-ray",
                        geometry: {
                          type: "LineString",
                          coordinates: [
                            [39.72, 43.58],
                            [39.721, 43.581],
                          ],
                        },
                        properties: {
                          landmark_id: "sea_terminal",
                          floor: 3,
                          facade: "W",
                          available: true,
                          terrain_blocked: false,
                          blocking_building_ids: ["osm-way-123456"],
                        },
                      },
                    ]
                  : [],
            },
            warnings: floor ? [] : ["Для лучей выберите известный этаж"],
          };
        } else if (/\/facts\/.+\/evidence$/.test(path)) {
          evidenceReads.push(path);
          const id = decodeURIComponent(
            path.split("/facts/")[1].replace(/\/evidence$/, ""),
          );
          const original = allFacts[i].find((fact) => fact.id === id);
          assert(original, `Unknown fixture evidence: ${id}`);
          const evidence = {
            id: `proof:${id}`,
            kind: "calculation",
            label: "Учебный набор с заранее известным ответом",
            source_ids: ["tz_fixture"],
            inputs: { fixture: true },
            output: original.value || {},
            limitations: [
              "Синтетический пример только для проверки интерфейса",
            ],
          };
          const full = { ...original, evidence: [evidence] };
          json = {
            fact: full,
            claims: [
              {
                id: original.supporting_claim_ids[0],
                hotel_id: hotels[i].id,
                characteristic_id: original.characteristic_id,
                value: original.value,
                scope: original.scope,
                source: "tz_fixture",
                method: "known_answer_fixture@1",
                evidence: [evidence],
                confidence: original.confidence,
                observed_at: "2026-10-27T12:00:00Z",
                valid_until: original.valid_until,
                synthetic: true,
              },
            ],
          };
        } else if (
          path.endsWith("/room-advice") ||
          path.endsWith("/rooms") ||
          path.endsWith("/photos")
        )
          json = [];
        else json = details[i];
      }
    }
    return route.fulfill({ json });
  });
  if (fixture) {
    await ctx.addInitScript(
      (value) => sessionStorage.setItem("okno-parsed", JSON.stringify(value)),
      parsed,
    );
    await ctx.route("**/data/**", (route) => {
      const path = new URL(route.request().url()).pathname;
      return route.fulfill(
        path.endsWith(".pmtiles") || /\.(pbf|webp|png)$/.test(path)
          ? { status: 404 }
          : {
              json: path.includes("photos")
                ? { photos: {} }
                : path.includes("snapshots")
                  ? { snapshots: {} }
                  : { type: "FeatureCollection", features: [] },
            },
      );
    });
  }
  const page = await ctx.newPage();
  page.on("pageerror", (error) => errors.push(error.message));
  return { ctx, page };
}
async function capture(page, name) {
  await page.screenshot({
    path: resolve(artifacts, `${name}.png`),
    fullPage: true,
  });
}
async function overflow(page) {
  assert(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth + 1,
    ),
    "Page overflows viewport",
  );
}
try {
  for (const [width, scheme] of [
    [1440, "light"],
    [390, "dark"],
    [390, "light"],
    [1440, "dark"],
  ]) {
    const { ctx, page } = await context(
      { width, height: width === 390 ? 844 : 1000 },
      scheme,
      true,
    );
    const evidenceBefore = evidenceReads.length;
    await page.goto(`${base}/hotel/${ids[0]}`);
    const place = page.getByTestId("place-facts");
    await place.waitFor();
    assert.equal(
      await page
        .getByTestId("travel-map")
        .getAttribute("data-landmark-ray-count"),
      "0",
      "No selected floor means no landmark rays",
    );
    assert.equal(
      evidenceReads.length,
      evidenceBefore,
      "Evidence must stay lazy",
    );
    assert.equal(
      await place.getAttribute("open"),
      null,
      "О месте must start closed",
    );
    assert.equal(
      await page.locator("[data-theme]").getAttribute("data-theme"),
      scheme,
    );
    await place.locator(":scope > summary").click();
    for (const id of Object.keys(names).filter(
      (id) =>
        ![
          "family_nearby",
          "car_access",
          "building_history",
          "climate",
        ].includes(id),
    ))
      assert(
        await page.getByTestId(`fact-card-${id}`).isVisible(),
        `Missing ${id}`,
      );
    const card = (id) => page.getByTestId(`fact-card-${id}`);
    assert.match(
      await card("mobile_internet").innerText(),
      /46 Мбит\/с.*не Wi-Fi отеля/,
    );
    assert.match(
      await card("mobile_masts").innerText(),
      /0 мачт.*качество связи неизвестно/,
    );
    assert.match(
      await card("photo_features").innerText(),
      /Нет явных сведений/,
    );
    assert.match(
      await card("construction_nearby").innerText(),
      /Текущие работы неизвестны.*206/,
    );
    assert.match(
      await card("veterinary_nearby").innerText(),
      /22 мин.*дальше 15 мин/,
    );
    assert.match(
      await card("pet_shop_nearby").innerText(),
      /пеший путь неизвестен/,
    );
    assert.match(
      await card("dog_walk").innerText(),
      /Обычный парк, не площадка/,
    );
    await page
      .getByTestId("other-hotel-facts")
      .locator(":scope > summary")
      .click();
    assert.match(await card("family_nearby").innerText(), /6 мин пешком/);
    await card("family_nearby").locator(".fact-details > summary").click();
    const familyObservation = card("family_nearby").locator(".tz-observation");
    assert.match(
      await familyObservation.getAttribute("data-fact-id"),
      /:routes$/,
    );
    await familyObservation.getByTestId("evidence-family_nearby").click();
    assert.match(await page.getByRole("dialog").innerText(), /6 мин пешком/);
    assert.match(
      decodeURIComponent(evidenceReads.at(-1)),
      /:routes\/evidence$/,
    );
    await page
      .getByRole("dialog")
      .getByLabel("Закрыть", { exact: true })
      .click();
    await card("family_nearby").locator(".fact-details > summary").click();
    assert.match(await card("car_access").innerText(), /8 мин пешком/);
    assert.match(
      await card("building_history").innerText(),
      /2010.*дата постройки не подтверждена/,
    );
    assert.match(await card("climate").innerText(), /солнечных дней.*20/i);
    const visible = await page.locator("body").innerText();
    assert(
      !/\d+%|\bдБ\b|osm-(?:way|node|relation)-/.test(visible),
      "Technical values leaked into first guest layer",
    );
    await overflow(page);
    await capture(page, `fixture-place-${width}-${scheme}`);
    await card("landmark_view").locator(".fact-details > summary").click();
    await card("landmark_view")
      .locator('[data-landmark-id="sea_terminal"] > summary')
      .click();
    await card("landmark_view").getByTestId("landmark-map").click();
    await page.waitForFunction(
      () =>
        document
          .querySelector('[data-testid="travel-map"]')
          ?.getAttribute("data-landmark-ray-count") === "1",
    );
    assert.equal(
      await page
        .getByTestId("travel-map")
        .getAttribute("data-selected-landmark"),
      "sea_terminal",
    );
    assert.equal(
      await page
        .getByTestId("travel-map")
        .getAttribute("data-landmark-target-count"),
      "1",
    );
    const proof = page.getByTestId("landmark-map-proof");
    await proof.locator(":scope > summary").click();
    assert.match(await proof.innerText(), /Высота модели по этажности/);
    assert(
      !/osm-way-123456/.test(await proof.innerText()),
      "Blocking IDs belong in proof only",
    );
    await proof.getByText("Для дотошных", { exact: true }).click();
    assert.match(await proof.innerText(), /osm-way-123456/);
    assert.match(
      await proof.innerText(),
      /osm_building_levels_3m_and_copernicus_dsm/,
    );
    await proof.evaluate((element) => {
      element.scrollTop = 0;
    });
    await overflow(page);
    await capture(page, `fixture-landmark-map-${width}-${scheme}`);
    await page.getByTestId("travel-map").screenshot({
      path: resolve(artifacts, `fixture-map-detail-${width}-${scheme}.png`),
    });
    await proof.locator(":scope > summary").click();
    await card("landmark_view").getByTestId("landmark-window").click();
    await page.getByTestId("room-lab-landmark-select").waitFor();
    assert.equal(await page.getByTestId("room-lab-facade").inputValue(), "W");
    assert.equal(await page.getByTestId("room-lab-floor").inputValue(), "3");
    assert.equal(
      await page.getByTestId("room-lab-landmark-select").inputValue(),
      "sea_terminal",
    );
    await page.getByTestId("room-lab-landmark-select").selectOption("fisht");
    assert.match(
      await page.getByTestId("room-lab-fact-landmark_view").innerText(),
      /видимость неизвестна/,
    );
    await page.waitForFunction(
      () =>
        document
          .querySelector('[data-testid="travel-map"]')
          ?.getAttribute("data-selected-landmark") === "fisht",
    );
    assert.equal(
      await page
        .getByTestId("travel-map")
        .getAttribute("data-landmark-ray-count"),
      "0",
      "Fisht without known height must not get a ray",
    );
    await page
      .getByTestId("landmark-map-proof")
      .locator(":scope > summary")
      .click();
    assert.match(
      await page.getByTestId("landmark-map-proof").innerText(),
      /Высота «Фишта» не подтверждена/,
    );
    await overflow(page);
    await capture(page, `fixture-window-${width}-${scheme}`);
    await page.goto(`${base}/compare?ids=${ids.join(",")}`);
    await page.locator(".comparison-table").waitFor();
    await page.getByRole("checkbox", { name: "Только различия" }).uncheck();
    const rowNames = await page
      .locator(".comparison-table tbody > tr > th")
      .allTextContents();
    assert(
      rowNames[0].includes(names.mobile_internet) &&
        rowNames[1].includes(names.night_open),
      "Requested criteria must lead comparison",
    );
    assert.match(
      await page.locator(".comparison-table").innerText(),
      /Своей парковки нет.*источник|Своей парковки нет по сообщению источника/,
    );
    assert.match(
      await page.locator(".comparison-table").innerText(),
      /дата постройки не подтверждена/,
    );
    assert.match(
      await page.locator(".comparison-table").innerText(),
      /История и возраст здания не подтверждены точным источником/,
    );
    const mobileRow = page.locator("tbody tr").filter({
      has: page.getByRole("rowheader", {
        name: names.mobile_internet,
        exact: false,
      }),
    });
    await mobileRow
      .locator("td")
      .first()
      .getByText("Подробнее и основания", { exact: true })
      .click();
    await mobileRow
      .locator("td")
      .first()
      .getByTestId("evidence-mobile_internet")
      .click();
    const modal = page.getByRole("dialog");
    await modal.getByText("Для дотошных", { exact: true }).click();
    await modal.getByText("Данные получены", { exact: true }).waitFor();
    assert.match(await modal.innerText(), /27\.10\.2026|27 окт/);
    assert.match(await modal.innerText(), /known_answer_fixture@1/);
    await modal.getByLabel("Закрыть", { exact: true }).click();
    await overflow(page);
    await capture(page, `fixture-compare-${width}-${scheme}`);
    await ctx.close();
    checks.push(
      `synthetic known-answer ${width}px ${scheme}: new facts, supplementary refs, unknown/no, target/window, comparison, evidence, no overflow`,
    );
  }
  if (live) {
    const response = await fetch(`${apiBase}/api/hotels`);
    assert(response.ok);
    const catalog = await response.json();
    assert(catalog.length > 1);
    const selected =
      catalog.find((hotel) => hotel.id === "osm-way-44573571") || catalog[0];
    const { ctx, page } = await context(
      { width: 1440, height: 1000 },
      "light",
      false,
    );
    await page.goto(`${base}/hotel/${selected.id}`);
    await page.getByTestId("place-facts").waitFor();
    assert.equal(
      await page.getByTestId("place-facts").getAttribute("open"),
      null,
    );
    await page.getByTestId("place-facts").locator(":scope > summary").click();
    await page.getByTestId("fact-card-mobile_internet").waitFor();
    await overflow(page);
    await capture(page, "live-place-desktop");
    await ctx.close();
    checks.push(`real API :8081 hotel ${selected.id}: place facts visible`);
  }
  assert.equal(
    mutations.length,
    0,
    `Unexpected writes: ${mutations.join(", ")}`,
  );
  assert.deepEqual(errors, []);
  assert(
    geoReads.some(
      (params) =>
        params.facade === "W" && params.floor === "3" && params.month === "9",
    ),
    "Selected map slice was not requested",
  );
  await writeFile(
    resolve(artifacts, "results.json"),
    JSON.stringify({ passed: true, live, checks, errors, mutations }, null, 2),
  );
  console.log(
    `PASS tz-gaps-smoke: ${checks.length} scenarios; ${live ? "live API and " : ""}synthetic known-answer fixtures; no writes`,
  );
} finally {
  await closeSmokeBrowser(browser);
}
process.exit(process.exitCode || 0);
