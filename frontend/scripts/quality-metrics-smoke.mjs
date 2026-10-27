import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { mkdir, readFile } from "node:fs/promises";
import { chromium } from "playwright";

const base = process.env.OKNO_BASE_URL || "http://localhost:8080";
const chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const executablePath =
  process.env.OKNO_BROWSER_EXECUTABLE ||
  (existsSync(chrome) ? chrome : undefined);
const rankingPath =
  process.env.OKNO_RANKING_REPORT ||
  new URL("../../data/eval/ranking_results.json", import.meta.url);
// Historical computed ranking aggregates are read, never recomputed here.
const ranking = JSON.parse(await readFile(rankingPath, "utf8"));
const artifacts = new URL(
  "../../runtime/night/quality-metrics-smoke/",
  import.meta.url,
);
await mkdir(artifacts, { recursive: true });
// Synthetic aggregate-only fixture, not a measured parser score or hotel claim.
// No language corpus is loaded or passed to a parser by this script.
const parser = {
  status: "measured_against_authored_intent_cases",
  gold: {
    queries: 239,
    exact_matches: 210,
    exact_match_rate: 210 / 239,
    criterion_precision: 0.93,
    criterion_recall: 0.88,
  },
  splits: {
    development: {
      queries: 189,
      exact_matches: 170,
      exact_match_rate: 170 / 189,
      criterion_precision: 0.94,
      criterion_recall: 0.9,
    },
    holdout: {
      queries: 50,
      exact_matches: 40,
      exact_match_rate: 0.8,
      criterion_precision: 0.91,
      criterion_recall: 0.83,
    },
  },
  negative: {
    queries: 120,
    queries_with_false_criteria: 11,
    false_criteria_rate: 11 / 120,
    criteria_emitted: 14,
  },
  corpus_sha256: { gold: "browser-fixture", negative: "browser-fixture" },
};
const fixture = {
  hotel_count: 250,
  coverage: { known_pairs: 0, total_pairs: 4250, ratio: 0 },
  manual_label_count: 0,
  question_share: null,
  label_groups: {},
  parser_accuracy: null,
  ndcg_at_5: null,
  status: "awaiting_independent_labels",
  generated_at: "2026-10-27T00:00:00Z",
  limitations: [
    "Браузерная fixture: агрегаты разбора синтетические, не реальные результаты измерения.",
  ],
  ranking_weak: {
    ...ranking,
    status: "measured_against_frozen_weak_labels",
    human_labeled: false,
  },
  parser_intent: parser,
};
const browser = await chromium.launch({ headless: true, executablePath });
try {
  const page = await browser.newPage({
    viewport: { width: 1440, height: 1000 },
  });
  const errors = [],
    failures = [],
    apiMethods = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("response", (response) => {
    if (response.url().startsWith(base) && response.status() >= 400)
      failures.push(`${response.status()} ${response.url()}`);
  });
  let current = structuredClone(fixture);
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    apiMethods.push(request.method());
    const pathname = new URL(request.url()).pathname;
    if (pathname === "/api/quality") return route.fulfill({ json: current });
    if (["/api/hotels", "/api/characteristics"].includes(pathname))
      return route.fulfill({ json: [] });
    if (pathname === "/api/revision")
      return route.fulfill({ json: { instance: "fixture", revision: 0 } });
    if (pathname === "/api/status")
      return route.fulfill({
        json: {
          name: "Quality browser fixture",
          mode: "offline",
          version: "fixture",
          hotel_count: 250,
          characteristic_count: 23,
          seed_version: "browser-fixture",
        },
      });
    throw new Error(`Unexpected API request in metric UI test: ${pathname}`);
  });
  const load = async () => {
    await page.goto(`${base}/quality`);
    await page.getByTestId("quality-summary").waitFor();
    assert.match(
      await page.getByTestId("quality-summary").innerText(),
      /Вручную проверено: 0/,
    );
    assert.equal(
      await page.getByTestId("ranking-weak").isVisible(),
      false,
      "Technical metrics start on the second level",
    );
    await page
      .getByText(
        "Подробности для команды и разработчиков: методика и метрики",
        { exact: true },
      )
      .click();
    await page.getByTestId("ranking-weak").waitFor();
  };
  await load();
  assert.match(
    await page.getByTestId("ranking-weak").innerText(),
    /Метки выведены из фактов, не человеческая оценка/,
  );
  for (const split of ["dev", "holdout"]) {
    const expected = ranking.by_split[split];
    const row = page.getByTestId(`ranking-weak-${split}`);
    await row.waitFor();
    const cells = await row.locator("td").allTextContents();
    const fmt = new Intl.NumberFormat("ru", { maximumFractionDigits: 3 });
    assert.equal(
      cells[0],
      typeof expected.ndcg_at_5 === "number"
        ? fmt.format(expected.ndcg_at_5)
        : "Не измерено",
    );
    assert.equal(
      cells[1],
      `${expected.queries_with_measured_ndcg} из ${expected.queries}`,
    );
    assert.equal(cells[2], String(expected.queries_without_positive_judgments));
    const bounds = expected.ndcg_at_5_bounds;
    assert.equal(
      cells[4],
      typeof bounds?.lower === "number" && typeof bounds?.upper === "number"
        ? `${fmt.format(bounds.lower)}–${fmt.format(bounds.upper)}`
        : "Не измерено",
    );
  }
  assert.match(
    await page.getByTestId("ranking-weak").innerText(),
    /полностью размеченным top-5/,
  );
  assert.match(
    await page.getByTestId("independent-ndcg").innerText(),
    /Не измерено/,
  );
  assert.equal(
    await page
      .getByTestId("ranking-weak")
      .getByRole("link")
      .getAttribute("href"),
    "/data/eval/ranking_results.json",
  );
  assert.match(await page.getByTestId("parser-intent-gold").innerText(), /239/);
  assert.match(
    await page.getByTestId("parser-intent-development").innerText(),
    /189/,
  );
  assert.match(
    await page.getByTestId("parser-intent-holdout").innerText(),
    /50/,
  );
  assert.match(
    await page.getByTestId("parser-intent-negative").innerText(),
    /120/,
  );
  assert.match(
    await page.getByTestId("parser-intent").innerText(),
    /не произвольная речь пользователей и не проверка фактов отелей/,
  );
  assert.equal(
    await page
      .getByTestId("parser-intent")
      .getByRole("link")
      .getAttribute("href"),
    "/data/eval/parser_results.json",
  );
  await page.getByTestId("ranking-weak").screenshot({
    path: new URL("ranking-measured-fixture.png", artifacts).pathname,
  });
  await page.getByTestId("parser-intent").screenshot({
    path: new URL("parser-measured-fixture.png", artifacts).pathname,
  });

  const rankingStatuses = [
    "scorer_changed_recompute_required",
    "gold_changed_recompute_required",
    "gold_changed",
    "not_measured",
  ];
  const parserStatuses = [
    "parser_changed_recompute_required",
    "catalogue_changed_recompute_required",
    "corpus_changed_recompute_required",
    "not_measured",
  ];
  for (let index = 0; index < rankingStatuses.length; index++) {
    current = structuredClone(fixture);
    current.ranking_weak.status = rankingStatuses[index];
    current.parser_intent.status = parserStatuses[index];
    // Deliberately keep the old numbers: the status must win over stale payloads.
    await load();
    await page.getByTestId("ranking-weak-unavailable").waitFor();
    await page.getByTestId("parser-intent-unavailable").waitFor();
    assert.equal(
      await page.getByTestId("ranking-weak").locator("table").count(),
      0,
    );
    assert.equal(
      await page
        .getByTestId("parser-intent")
        .locator("table, .metric-row")
        .count(),
      0,
    );
    assert.doesNotMatch(
      await page.getByTestId("parser-intent").innerText(),
      /239|189|120/,
    );
    assert.match(
      await page.getByTestId("independent-ndcg").innerText(),
      /Не измерено/,
    );
    if (index === 0)
      await page.screenshot({
        path: new URL("stale-fixture.png", artifacts).pathname,
        fullPage: true,
      });
  }
  current = structuredClone(fixture);
  delete current.parser_intent;
  await load();
  assert.equal(
    await page.getByTestId("parser-intent").count(),
    0,
    "An absent optional report produces no parser block",
  );
  current.ranking_weak.human_labeled = true;
  await load();
  assert.equal(
    await page.getByTestId("ranking-weak").locator("table").count(),
    0,
    "Unexpected label provenance cannot be presented as fact-derived weak labels",
  );

  current = structuredClone(fixture);
  await page.setViewportSize({ width: 390, height: 844 });
  await load();
  await page.getByTestId("parser-intent-holdout").waitFor();
  assert.equal(
    await page.evaluate(() => document.documentElement.scrollWidth),
    390,
    "Metric tables must scroll internally without mobile document overflow",
  );
  assert.equal(await page.getByTestId("ranking-weak-dev").count(), 1);
  assert.equal(await page.getByTestId("ranking-weak-holdout").count(), 1);
  await page.getByTestId("ranking-weak").screenshot({
    path: new URL("ranking-mobile-fixture.png", artifacts).pathname,
  });
  await page.getByTestId("parser-intent").screenshot({
    path: new URL("parser-mobile-fixture.png", artifacts).pathname,
  });
  assert.deepEqual(errors, [], "No uncaught browser errors");
  assert.deepEqual(failures, [], "No broken same-origin assets");
  assert(
    apiMethods.every((method) => method === "GET"),
    "Quality metrics are read-only",
  );
  console.log(
    JSON.stringify(
      {
        status: "passed",
        base,
        fixture_only: true,
        ranking_source: String(rankingPath),
        checks: [
          "weak-split-coverage-bounds",
          "independent-ndcg-unmeasured",
          "authored-parser-splits-negatives",
          "stale-metrics-hidden",
          "optional-parser-absent",
          "label-provenance-guard",
          "mobile-no-overflow",
          "read-only",
        ],
        screenshots: artifacts.pathname,
      },
      null,
      2,
    ),
  );
} finally {
  await browser.close();
}
