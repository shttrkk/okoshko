import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";
import { blockYandex, closeSmokeBrowser } from "./panel-guest-smoke-ui.mjs";

const base = process.env.OKNO_BASE_URL || "http://localhost:8080";
const query = "тихий номер с видом на закат недалеко от моря";
const chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const executablePath =
  process.env.OKNO_BROWSER_EXECUTABLE ||
  (existsSync(chrome) ? chrome : undefined);
const artifacts = resolve("../runtime/risk-context-smoke");
await mkdir(artifacts, { recursive: true });
const response = await fetch(`${base}/api/search`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ text: query, limit: 40, compact: true }),
});
assert(response.ok);
const bytes = await response.arrayBuffer();
assert(
  bytes.byteLength < 400_000,
  "Guest page stays within its documented byte budget",
);
const search = JSON.parse(new TextDecoder().decode(bytes));
const hit = search.results.find((hit) =>
  hit.verdicts.some((v) => v.context_fact_ids?.length),
);
assert(
  hit,
  "Actual search includes a current mapped risk alongside a requested condition",
);
const verdict = hit.verdicts.find((v) => v.context_fact_ids?.length);
const hotelResponse = await fetch(
  `${base}/api/hotels/${encodeURIComponent(hit.hotel.id)}`,
);
assert(hotelResponse.ok);
const hotel = await hotelResponse.json();
const facts = new Map(hotel.facts.map((f) => [f.id, f]));
assert(verdict.fact_ids.length > 0);
assert(
  verdict.fact_ids.every(
    (id) => facts.get(id)?.characteristic_id === verdict.characteristic_id,
  ),
  "Supporting facts belong to the condition, never to its risk context",
);
assert(
  verdict.context_fact_ids.every(
    (id) => ["airport_noise", "nightlife"].includes(facts.get(id)?.characteristic_id),
  ),
  "Requested quietness may have airport and mapped nearby-bar context",
);
assert(!verdict.fact_ids.some((id) => verdict.context_fact_ids.includes(id)));
assert(
  verdict.context_evidence_refs.every((id) =>
    verdict.context_fact_ids.some((fid) =>
      facts.get(fid).evidence.some((e) => e.id === id),
    ),
  ),
);
const endpoint = `/api/hotels/${encodeURIComponent(hit.hotel.id)}/facts/${encodeURIComponent(verdict.context_fact_ids[0])}/evidence`;
const firstContextKind = facts.get(verdict.context_fact_ids[0]).characteristic_id;
const contextText = firstContextKind === "airport_noise"
  ? /аэропорт|полос|самол[её]т/i
  : /бар|кафе|заведени|событи/i;
const browser = await chromium.launch({ headless: true, executablePath });
const checks = [];
try {
  for (const [width, colorScheme] of [
    [1440, "light"],
    [390, "dark"],
  ]) {
    const context = await browser.newContext({
      viewport: { width, height: 900 },
      colorScheme,
    });
    await blockYandex(context);
    const requests = [],
      errors = [],
      forbidden = [];
    await context.route("**/api/**", (route) => {
      const request = route.request(),
        path = new URL(request.url()).pathname;
      if (
        !["GET", "HEAD", "OPTIONS"].includes(request.method()) &&
        !(
          request.method() === "POST" &&
          (path === "/api/search" || /\/evaluate$/.test(path))
        )
      ) {
        forbidden.push(path);
        return route.abort();
      }
      return route.continue();
    });
    const page = await context.newPage();
    page.on("request", (req) => requests.push(new URL(req.url()).pathname));
    page.on("pageerror", (err) => errors.push(err.message));
    await page.goto(`${base}/search?q=${encodeURIComponent(query)}`);
    const card = page.getByTestId(`hotel-card-${hit.hotel.id}`);
    await card.waitFor();
    assert(
      !requests.includes(endpoint),
      "Risk evidence is lazy before the click",
    );
    const proof = page.waitForResponse(
      (res) => new URL(res.url()).pathname === endpoint,
    );
    await card.getByTestId("context-evidence").first().click();
    assert((await proof).ok());
    const modal = page.getByRole("dialog");
    await modal.waitFor();
    assert.match(await modal.innerText(), contextText);
    assert(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth + 1,
      ),
    );
    await page.screenshot({
      path: resolve(artifacts, `${width}-${colorScheme}.png`),
      fullPage: true,
    });
    await modal.getByRole("button", { name: "Закрыть", exact: true }).click();
    assert.deepEqual(errors, []);
    assert.deepEqual(forbidden, []);
    checks.push({
      width,
      colorScheme,
      lazy_evidence: true,
      no_writes: true,
      no_overflow: true,
    });
    await context.close();
  }
  const report = {
    passed: true,
    hotel_id: hit.hotel.id,
    criterion: verdict.characteristic_id,
    context_fact_ids: verdict.context_fact_ids,
    context_characteristics: verdict.context_fact_ids.map((id) => facts.get(id).characteristic_id),
    response_bytes: bytes.byteLength,
    checks,
  };
  await writeFile(
    resolve(artifacts, "results.json"),
    JSON.stringify(report, null, 2) + "\n",
  );
  console.log(JSON.stringify(report, null, 2));
} finally {
  await closeSmokeBrowser(browser);
}
process.exit(0);
