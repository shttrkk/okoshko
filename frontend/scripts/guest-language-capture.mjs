import { chromium } from "playwright";
import { blockYandex, closeSmokeBrowser } from "./panel-guest-smoke-ui.mjs";
import { existsSync } from "node:fs";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import assert from "node:assert/strict";

export const guestQuery = "тихий номер с видом на закат недалеко от моря";
export const primorskayaId = "overture-037ee676-5776-46bd-a4ac-38c828f2a99d";
const systemChrome =
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";

export function scopeIsSupported(
  option,
  facts,
  { hotelId, characteristicId, month = 9 },
) {
  const scope = option?.scope;
  if (!scope || !Array.isArray(facts) || !facts.length) return false;
  const bounds = (value) => {
    const min = value.floor_min ?? 1;
    const max = value.floor_max ?? Infinity;
    return Number.isInteger(min) &&
      min >= 1 &&
      (Number.isInteger(max) || max === Infinity) &&
      min <= max
      ? [min, max]
      : null;
  };
  const requested = bounds(scope);
  if (!requested) return false;
  const covered = [];
  const numericCovered = [];
  for (const fact of facts) {
    if (
      !fact ||
      fact.hotel_id !== hotelId ||
      fact.characteristic_id !== characteristicId ||
      ["unknown", "stale", "disputed", "conflict", "conflicted"].includes(
        fact.status,
      ) ||
      /conflict|disput/iu.test(fact.resolution || "") ||
      fact.conflicting_claim_ids?.length ||
      !(Date.parse(fact.valid_until) > Date.now())
    )
      return false;
    if (
      ["sea_view", "morning_sun", "sunset_view"].includes(characteristicId) &&
      fact.value?.available !== true
    )
      return false;
    let numericNoise = false;
    if (characteristicId === "quiet_night") {
      const min = fact.value?.night_db_min;
      const max = fact.value?.night_db_max;
      numericNoise = Number.isFinite(min) && Number.isFinite(max) && min <= max;
      // A numeric exterior-noise measurement remains evidence when available
      // is false. It does not establish a boolean promise of indoor silence.
      if ((min !== undefined || max !== undefined) && !numericNoise)
        return false;
      if (!numericNoise && fact.value?.available !== true) return false;
    }
    const factScope = fact.scope || {};
    if (
      (factScope.facade && factScope.facade !== scope.facade) ||
      (factScope.month && factScope.month !== (scope.month ?? month)) ||
      (factScope.room_type && factScope.room_type !== scope.room_type)
    )
      return false;
    const interval = bounds(factScope);
    if (!interval) return false;
    const min = Math.max(requested[0], interval[0]);
    const max = Math.min(requested[1], interval[1]);
    // An unrelated reference must fail even when other references already
    // cover all requested floors.
    if (min > max) return false;
    covered.push([min, max]);
    if (numericNoise) numericCovered.push([min, max]);
  }
  const covers = (intervals) => {
    intervals.sort((left, right) => left[0] - right[0]);
    let lastCovered = requested[0] - 1;
    for (const [min, max] of intervals) {
      if (min > lastCovered + 1) return false;
      lastCovered = Math.max(lastCovered, max);
    }
    return lastCovered >= requested[1];
  };
  // A room answer can corroborate the numeric observation, but it cannot
  // supply decibels for a floor interval without a numeric observation.
  return (
    covers(covered) &&
    (characteristicId !== "quiet_night" || covers(numericCovered))
  );
}

export function scopeSupportSelfChecks() {
  // Pure regression cases only; every browser page still uses the real API.
  const hotelId = "scope-self-check";
  const characteristicId = "sea_view";
  const option = {
    scope: { facade: "west", floor_min: 1, floor_max: 5, month: 9 },
  };
  const fact = (min, max, overrides = {}) => ({
    hotel_id: hotelId,
    characteristic_id: characteristicId,
    status: "computed",
    resolution: "single",
    valid_until: "2100-01-01T00:00:00Z",
    conflicting_claim_ids: [],
    value: { available: true },
    scope: { facade: "west", floor_min: min, floor_max: max, month: 9 },
    ...overrides,
  });
  const context = { hotelId, characteristicId };
  const cases = [
    ["adjacent-union", [fact(1, 3), fact(4, 5)], true],
    ["floor-gap", [fact(1, 2), fact(4, 5)], false],
    [
      "wrong-side",
      [fact(1, 5, { scope: { ...option.scope, facade: "east" } })],
      false,
    ],
    [
      "wrong-month",
      [fact(1, 5, { scope: { ...option.scope, month: 8 } })],
      false,
    ],
    ["wrong-hotel", [fact(1, 5, { hotel_id: "another-hotel" })], false],
    [
      "wrong-characteristic",
      [fact(1, 5, { characteristic_id: "sunset_view" })],
      false,
    ],
    ["disjoint-reference", [fact(1, 5), fact(6, 7)], false],
    [
      "expired-reference",
      [fact(1, 5, { valid_until: "2000-01-01T00:00:00Z" })],
      false,
    ],
    [
      "conflicting-reference",
      [fact(1, 5, { conflicting_claim_ids: ["conflict"] })],
      false,
    ],
    [
      "boolean-unavailable",
      [fact(1, 5, { value: { available: false } })],
      false,
    ],
  ];
  for (const [name, facts, expected] of cases)
    assert.equal(scopeIsSupported(option, facts, context), expected, name);
  assert.equal(
    scopeIsSupported(
      option,
      [
        fact(1, 5, {
          characteristic_id: "quiet_night",
          value: { available: false, night_db_min: 49, night_db_max: 58 },
        }),
      ],
      { hotelId, characteristicId: "quiet_night" },
    ),
    true,
    "numeric-noise-unavailable-is-still-data",
  );
  assert.equal(
    scopeIsSupported(
      option,
      [
        fact(1, 5, {
          characteristic_id: "quiet_night",
          value: { available: true, night_db_min: 58, night_db_max: 49 },
        }),
      ],
      { hotelId, characteristicId: "quiet_night" },
    ),
    false,
    "reversed-numeric-noise-is-invalid",
  );
  const noiseFact = (min, max, value) =>
    fact(min, max, { characteristic_id: "quiet_night", value });
  const noiseCases = [
    [
      "numeric-plus-agreeing-room-answer",
      [
        noiseFact(1, 5, { night_db_min: 34, night_db_max: 50 }),
        noiseFact(5, 5, { available: true }),
      ],
      true,
    ],
    [
      "boolean-answer-does-not-fill-numeric-gap",
      [
        noiseFact(1, 4, { night_db_min: 34, night_db_max: 50 }),
        noiseFact(5, 5, { available: true }),
      ],
      false,
    ],
    [
      "boolean-answer-without-numbers",
      [noiseFact(1, 5, { available: true })],
      false,
    ],
    [
      "numeric-plus-contradicting-room-answer",
      [
        noiseFact(1, 5, { night_db_min: 34, night_db_max: 50 }),
        noiseFact(5, 5, { available: false }),
      ],
      false,
    ],
  ];
  for (const [name, facts, expected] of noiseCases)
    assert.equal(
      scopeIsSupported(option, facts, {
        hotelId,
        characteristicId: "quiet_night",
      }),
      expected,
      name,
    );
  return {
    status: "passed",
    checks: [
      ...cases.map(([name]) => name),
      "numeric-noise-unavailable-is-still-data",
      "reversed-numeric-noise-is-invalid",
      ...noiseCases.map(([name]) => name),
    ],
  };
}

export function sentences(text) {
  // A line without final punctuation is still a complete UI phrase. Table
  // cells are separate phrases (innerText separates them with tabs). Decimal
  // points (e.g. 0.75) must not turn one sentence into shorter fragments.
  return text
    .split(/[\n\t]+/u)
    .flatMap((line) => {
      const parts = [];
      let start = 0;
      for (let i = 0; i < line.length; i += 1) {
        if (!/[.!?]/u.test(line[i])) continue;
        if (
          line[i] === "." &&
          /\d/u.test(line[i - 1] || "") &&
          /\d/u.test(line[i + 1] || "")
        )
          continue;
        parts.push(line.slice(start, i + 1));
        start = i + 1;
      }
      parts.push(line.slice(start));
      return parts;
    })
    .map((part) => part.replace(/\s+/gu, " ").trim())
    .filter(Boolean);
}

export function languageFindings(text) {
  const phrases = sentences(text);
  const counts = new Map();
  for (const phrase of phrases) {
    if (phrase.length <= 40) continue;
    const key = phrase.toLocaleLowerCase("ru");
    const item = counts.get(key) || { phrase, count: 0 };
    item.count += 1;
    counts.set(key, item);
  }
  const forbidden = [
    /Предварительная оценка с низкой уверенностью/giu,
    /[\p{L}-]*модельн[\p{L}-]*/giu,
    /[\p{L}-]*геомодел[\p{L}-]*/giu,
    /геометрическ[\p{L}-]*[^\n.!?]{0,100}модел[\p{L}-]*/giu,
    /[\p{L}-]*синтет[\p{L}-]*/giu,
    /[\p{L}-]*прокси[\p{L}-]*/giu,
    /[\p{L}-]*нормаль[\p{L}-]*/giu,
    /PMTiles/giu,
    /[\p{L}-]*экспозиц[\p{L}-]*/giu,
    /индекс 0–1/giu,
    /[\p{L}-]*картированн[\p{L}-]*/giu,
    /по OSM/giu,
    /<20/gu,
    /\boffline\b/gu,
    /видны море/giu,
    /СИМУЛЯЦИЯ/giu,
    /Синтетический ответ для демонстрации/giu,
    /hotelier_simulator/giu,
    /synthetic-demo/giu,
    /фикстур[\p{L}-]*/giu,
    /demo truth/giu,
    /Вымышленный ответ/giu,
    /(?:Надёжность|Надежность|уверенность)[^\n.!?]{0,40}\d+\s*%/giu,
    /условный уровень/giu,
    /по модели/giu,
    /\b(?:local_poi_nightlife@\d+|geo_engine)\b/giu,
    /POI-расчёт/giu,
    /opening_hours/giu,
    /(?:\d+(?:[.,]\d+)?\s*дБ|\d+(?:[.,]\d+)?\s*°)/giu,
    /Данных нет\s+Данных нет/giu,
  ];
  const forbiddenPhrases = [];
  for (const phrase of phrases) {
    const words = forbidden
      .flatMap((pattern) => [...phrase.matchAll(pattern)])
      .map((match) => match[0]);
    if (!words.length) continue;
    const item = forbiddenPhrases.find((entry) => entry.phrase === phrase);
    if (item) item.count += 1;
    else
      forbiddenPhrases.push({ phrase, words: [...new Set(words)], count: 1 });
  }
  const frequentWords = new Map();
  for (const word of text.toLocaleLowerCase("ru").match(/[\p{L}]{4,}/gu) || [])
    frequentWords.set(word, (frequentWords.get(word) || 0) + 1);
  const emptyBeachQuotes = new Map();
  for (const match of text.matchAll(/«\s*»|""/gu)) {
    const start = text.lastIndexOf("\n", match.index - 1) + 1;
    const nextLine = text.indexOf("\n", match.index + match[0].length);
    const phrase = text
      .slice(start, nextLine === -1 ? undefined : nextLine)
      .replace(/\s+/gu, " ")
      .trim();
    if (
      !/пляж|пешком|маршрут|\d+(?:[.,]\d+)?\s*(?:км|м|мин)(?:\s|$)|до\s*[«"]/iu.test(
        phrase,
      )
    )
      continue;
    const item = emptyBeachQuotes.get(phrase) || { phrase, count: 0 };
    item.count += 1;
    emptyBeachQuotes.set(phrase, item);
  }
  return {
    forbiddenPhrases,
    emptyBeachQuotes: [...emptyBeachQuotes.values()],
    repeatedPhrases: [...counts.values()]
      .filter((item) => item.count > 2)
      .sort((a, b) => b.count - a.count),
    frequentWords: [...frequentWords]
      .sort((a, b) => b[1] - a[1])
      .slice(0, 20)
      .map(([word, count]) => ({ word, count })),
  };
}

export async function visibleSnapshot(page) {
  return page.evaluate(() => {
    // Chromium innerText uses rendered text, including only the summary of a
    // closed native details. Never scan textContent or aria-labels.
    const innerText = document.body.innerText;
    const rect = (element) => {
      if (!element) return null;
      const { x, y, width, height } = element.getBoundingClientRect();
      return { x, y, width, height };
    };
    const textarea = document.querySelector("textarea");
    // The example row under the input starts where the typed text starts:
    // a bordered button aligns by its edge, a plain text link by its text.
    const helperTarget = ".search-example-chips > :first-child";
    const helper = document.querySelector(helperTarget);
    const helperRect = rect(helper);
    const textareaRect = rect(textarea);
    const textareaStyle = textarea ? getComputedStyle(textarea) : null;
    const textareaTextStart =
      textareaRect && textareaStyle
        ? textareaRect.x +
          parseFloat(textareaStyle.borderLeftWidth) +
          parseFloat(textareaStyle.paddingLeft)
        : null;
    let helperTextStart = null;
    if (helper && !helper.classList.contains("text-button"))
      helperTextStart = helperRect.x;
    else if (helper) {
      const text = [...helper.childNodes].find(
        (node) => node.nodeType === Node.TEXT_NODE && node.textContent.trim(),
      );
      if (text) {
        const range = document.createRange();
        range.selectNodeContents(text);
        helperTextStart = range.getBoundingClientRect().x;
      }
    }
    const headings = [...document.querySelectorAll("h1,h2,h3,h4")];
    const roomHeading = headings.find((element) =>
      /Какой номер просить/iu.test(element.innerText),
    );
    const rawAdviceElement = document.querySelector(
      '[data-testid="room-advice"]',
    );
    const adviceElement = rawAdviceElement?.getClientRects().length
      ? rawAdviceElement
      : null;
    const adviceSection =
      roomHeading?.closest("section") || roomHeading?.parentElement;
    return {
      innerText,
      title: document.title,
      url: location.href,
      theme: localStorage.getItem("okno-theme"),
      scrollWidth: document.documentElement.scrollWidth,
      viewportWidth: innerWidth,
      documentHeight: document.documentElement.scrollHeight,
      closedDetails: [...document.querySelectorAll("details:not([open])")].map(
        (element) => ({
          summary: element.querySelector(":scope > summary")?.innerText || "",
        }),
      ),
      helper: {
        target: helperTarget,
        className: helper?.className || null,
        rect: helperRect,
        textarea: textareaRect,
        textareaTextStart,
        helperTextStart,
        leftOffset:
          helperTextStart !== null && textareaTextStart !== null
            ? helperTextStart - textareaTextStart
            : null,
      },
      roomAdvice: (adviceElement || adviceSection)?.innerText || null,
      roomAdviceHeading: roomHeading?.innerText || null,
      roomAdviceSameSection: Boolean(
        adviceElement && adviceSection?.contains(adviceElement),
      ),
    };
  });
}

async function waitForGuestPage(page, name) {
  if (name === "home") {
    await page.getByTestId("search-submit").waitFor();
    await page.locator('[data-map-status="ready"]').waitFor({ timeout: 45000 });
  } else if (name === "search") {
    await page.locator(".result-card").first().waitFor({ timeout: 45000 });
  } else {
    await page.locator(".hotel-title-row h1").waitFor({ timeout: 45000 });
    await page.getByTestId("related-hotels").waitFor({ timeout: 45000 });
    await page
      .getByText("Сопоставляем известные профили…", { exact: true })
      .waitFor({ state: "hidden", timeout: 45000 });
  }
  await page.evaluate(() => document.fonts.ready);
  await page
    .waitForLoadState("networkidle", { timeout: 15000 })
    .catch(() => {});
  // The map animates briefly after readiness; allow its first stable frame.
  await page.waitForTimeout(300);
}

export async function captureGuestPages({
  base = process.env.OKNO_BASE_URL || "http://localhost:8080",
  artifacts,
  onPage,
}) {
  const executablePath = process.env.OKNO_BROWSER_EXECUTABLE || systemChrome;
  if (!existsSync(executablePath))
    throw new Error(`System Chrome does not exist: ${executablePath}`);
  await mkdir(artifacts, { recursive: true });
  const browser = await chromium.launch({ headless: true, executablePath });
  const states = [],
    requests = [],
    responses = [],
    errors = [];
  try {
    for (const viewport of [
      { width: 1440, height: 1000 },
      { width: 390, height: 844 },
    ]) {
      for (const theme of ["light", "dark"]) {
        const context = await browser.newContext({
          viewport,
          colorScheme: theme,
          isMobile: viewport.width === 390,
          hasTouch: viewport.width === 390,
        });
        await blockYandex(context);
        await context.addInitScript((value) => {
          localStorage.setItem("okno-theme", value);
        }, theme);
        const page = await context.newPage();
        await blockYandex(page);
        let stateName;
        page.on("pageerror", (error) =>
          errors.push({ state: stateName, message: error.message }),
        );
        page.on("request", (request) => {
          if (request.url().startsWith(`${base}/api/`))
            requests.push({
              state: stateName,
              method: request.method(),
              url: request.url(),
              body: request.postData(),
            });
        });
        page.on("response", (response) => {
          if (response.url().startsWith(base) && response.status() >= 400)
            responses.push({
              state: stateName,
              status: response.status(),
              url: response.url(),
            });
        });
        let firstHotelHref;
        for (const name of ["home", "search", "first-hotel", "primorskaya"]) {
          stateName = `${name}-${viewport.width}x${viewport.height}-${theme}`;
          if (name === "home") await page.goto(base);
          else if (name === "search")
            await page.goto(
              `${base}/search?q=${encodeURIComponent(guestQuery)}`,
            );
          else if (name === "first-hotel") {
            firstHotelHref = await page
              .locator('.result-card a[href^="/hotel/"]')
              .first()
              .getAttribute("href");
            await page.getByTestId("open-hotel-0").click();
          } else await page.goto(`${base}/hotel/${primorskayaId}`);
          await waitForGuestPage(page, name);
          const snapshot = await visibleSnapshot(page);
          const state = {
            name: stateName,
            page: name,
            viewport,
            theme,
            firstHotelHref,
            ...snapshot,
            ...languageFindings(snapshot.innerText),
          };
          states.push(state);
          await writeFile(
            resolve(artifacts, `${stateName}.txt`),
            `${snapshot.innerText}\n`,
          );
          await writeFile(
            resolve(artifacts, `${stateName}.json`),
            `${JSON.stringify(state, null, 2)}\n`,
          );
          await page.screenshot({
            path: resolve(artifacts, `${stateName}.png`),
            fullPage: false,
            animations: "disabled",
          });
          if (onPage) await onPage(page, state);
        }
        await context.close();
      }
    }
  } finally {
    await closeSmokeBrowser(browser);
  }
  const report = {
    base,
    capturedAt: new Date().toISOString(),
    executablePath,
    states,
    requests,
    responses,
    errors,
  };
  await writeFile(
    resolve(artifacts, "summary.json"),
    `${JSON.stringify(report, null, 2)}\n`,
  );
  return report;
}

if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(resolve(process.argv[1])).href
) {
  const artifacts = resolve(
    process.env.OKNO_GUEST_ARTIFACTS || "runtime/guest-language-capture",
  );
  const report = await captureGuestPages({ artifacts });
  console.log(
    JSON.stringify(
      {
        artifacts,
        states: report.states.length,
        errors: report.errors,
        responses: report.responses,
      },
      null,
      2,
    ),
  );
  process.exit(process.exitCode || 0);
}
