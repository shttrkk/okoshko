import { chromium } from "playwright";
import { existsSync } from "node:fs";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

export const guestQuery = "тихий номер с видом на закат недалеко от моря";
export const primorskayaId = "overture-037ee676-5776-46bd-a4ac-38c828f2a99d";
const systemChrome =
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";

export function sentences(text) {
  // A line without final punctuation is still a complete UI phrase. Decimal
  // points (e.g. 0.75) must not turn one phrase into several shorter fragments.
  return text
    .split(/\n+/u)
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
    /[\p{L}-]*модельн[\p{L}-]*/giu,
    /[\p{L}-]*геомодел[\p{L}-]*/giu,
    /геометрическ[\p{L}-]*[^\n.!?]{0,100}модел[\p{L}-]*/giu,
    /[\p{L}-]*синтет[\p{L}-]*/giu,
    /[\p{L}-]*прокси[\p{L}-]*/giu,
    /[\p{L}-]*нормаль[\p{L}-]*/giu,
    /PMTiles/giu,
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
    const helper = [...document.querySelectorAll("body *")].findLast(
      (element) =>
        element.innerText?.trim() === "Одна фраза вместо десятка фильтров",
    );
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
    if (helper) {
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
      roomAdvice:
        (
          document.querySelector('[data-testid="room-advice"]') ||
          roomHeading?.closest("section") ||
          roomHeading?.parentElement
        )?.innerText || null,
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
        await context.addInitScript((value) => {
          localStorage.setItem("okno-theme", value);
        }, theme);
        const page = await context.newPage();
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
    await browser.close();
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
}
