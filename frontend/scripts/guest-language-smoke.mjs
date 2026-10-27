import { writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import {
  captureGuestPages,
  guestQuery,
  primorskayaId,
} from "./guest-language-capture.mjs";

// Uses the running application's API and seeded hotel catalogue. No fixtures,
// route.fulfill, passport creation, question submission or other writes.
// Run from frontend: node scripts/guest-language-smoke.mjs
const base = process.env.OKNO_BASE_URL || "http://localhost:8080";
const artifacts = resolve(
  process.env.OKNO_GUEST_ARTIFACTS || "../runtime/guest-language-smoke",
);
const failures = [],
  apiChecks = [];
const checkedHotels = new Map();
function check(condition, state, code, detail) {
  if (!condition) failures.push({ state, code, detail });
}
function nonemptyStrings(value) {
  return (
    Array.isArray(value) &&
    value.length > 0 &&
    value.every((item) => typeof item === "string" && item.length > 0)
  );
}
function hasFifthFloor(scope) {
  return (
    Number.isInteger(scope?.floor_min) &&
    Number.isInteger(scope?.floor_max) &&
    scope.floor_min <= 5 &&
    scope.floor_max >= 5
  );
}
function scopeIsSupported(option, fact) {
  const scope = option.scope;
  const factScope = fact.scope || {};
  if (factScope.facade && factScope.facade !== scope.facade) return false;
  if (factScope.month && factScope.month !== (scope.month || 9)) return false;
  if (
    factScope.floor_min !== null &&
    factScope.floor_min !== undefined &&
    (!Number.isInteger(scope.floor_min) ||
      scope.floor_min < factScope.floor_min)
  )
    return false;
  if (
    factScope.floor_max !== null &&
    factScope.floor_max !== undefined &&
    (!Number.isInteger(scope.floor_max) ||
      scope.floor_max > factScope.floor_max)
  )
    return false;
  return true;
}

async function checkRoomAdvice(page, state, hotelId) {
  if (checkedHotels.has(hotelId)) return checkedHotels.get(hotelId);
  const endpoint = `${base}/api/hotels/${hotelId}/room-advice?month=9`;
  const response = await page.request.get(endpoint);
  check(response.ok(), state.name, "room-advice-http", {
    url: endpoint,
    status: response.status(),
    body: response.ok() ? undefined : (await response.text()).slice(0, 400),
  });
  if (!response.ok()) {
    checkedHotels.set(hotelId, null);
    return null;
  }
  const advice = await response.json();
  check(Array.isArray(advice), state.name, "room-advice-array", advice);
  if (!Array.isArray(advice)) {
    checkedHotels.set(hotelId, null);
    return null;
  }
  check(advice.length <= 4, state.name, "room-advice-limit", advice.length);
  const detailResponse = await page.request.get(
    `${base}/api/hotels/${hotelId}`,
  );
  check(detailResponse.ok(), state.name, "hotel-detail-http", {
    hotelId,
    status: detailResponse.status(),
  });
  const detail = detailResponse.ok() ? await detailResponse.json() : {};
  const facts = new Map((detail.facts || []).map((fact) => [fact.id, fact]));
  for (const item of advice) {
    const itemId = `${hotelId}/${item.id || "missing-id"}`;
    check(
      ["id", "characteristic_id", "title", "text"].every(
        (key) => typeof item[key] === "string" && item[key].trim(),
      ) &&
        ["yes", "partial"].includes(item.status) &&
        typeof item.synthetic === "boolean" &&
        Array.isArray(item.caveats) &&
        item.caveats.every((value) => typeof value === "string"),
      state.name,
      "room-advice-shape",
      item,
    );
    check(
      nonemptyStrings(item.fact_ids) && nonemptyStrings(item.evidence_refs),
      state.name,
      "room-advice-refs",
      { itemId, fact_ids: item.fact_ids, evidence_refs: item.evidence_refs },
    );
    check(
      Array.isArray(item.options) && item.options.length > 0,
      state.name,
      "room-advice-options",
      { itemId, options: item.options },
    );
    for (const [index, option] of (item.options || []).entries()) {
      const optionId = `${itemId}/option-${index}`;
      const scope = option.scope;
      check(
        scope &&
          typeof scope === "object" &&
          !Array.isArray(scope) &&
          ["yes", "partial"].includes(option.status) &&
          typeof option.synthetic === "boolean" &&
          (scope.facade === null || typeof scope.facade === "string") &&
          (scope.month === null || scope.month === 9) &&
          [scope.floor_min, scope.floor_max].every(
            (floor) =>
              floor === null || (Number.isInteger(floor) && floor >= 1),
          ) &&
          (scope.floor_min === null ||
            scope.floor_max === null ||
            scope.floor_min <= scope.floor_max),
        state.name,
        "room-advice-option-shape",
        { optionId, option },
      );
      check(
        nonemptyStrings(option.fact_ids) &&
          nonemptyStrings(option.evidence_refs),
        state.name,
        "room-advice-option-refs",
        { optionId, option },
      );
      const optionFacts = (option.fact_ids || []).map((id) => facts.get(id));
      check(
        optionFacts.length > 0 && optionFacts.every(Boolean),
        state.name,
        "room-advice-option-existing-facts",
        {
          optionId,
          missing: (option.fact_ids || []).filter((id) => !facts.has(id)),
        },
      );
      const evidenceIds = new Set(
        optionFacts.flatMap((fact) =>
          (fact?.evidence || []).map((entry) => entry.id),
        ),
      );
      check(
        (option.evidence_refs || []).every((id) => evidenceIds.has(id)),
        state.name,
        "room-advice-option-existing-evidence",
        {
          optionId,
          missing: (option.evidence_refs || []).filter(
            (id) => !evidenceIds.has(id),
          ),
        },
      );
      check(
        optionFacts.every(
          (fact) =>
            fact &&
            scope &&
            !["unknown", "stale", "disputed"].includes(fact.status) &&
            Date.parse(fact.valid_until) > Date.now() &&
            scopeIsSupported(option, fact),
        ),
        state.name,
        "room-advice-option-supported-scope",
        { optionId, scope, fact_ids: option.fact_ids },
      );
      check(
        (option.fact_ids || []).every((id) =>
          (item.fact_ids || []).includes(id),
        ) &&
          (option.evidence_refs || []).every((id) =>
            (item.evidence_refs || []).includes(id),
          ) &&
          option.synthetic ===
            optionFacts.some((fact) => fact?.synthetic === true),
        state.name,
        "room-advice-option-provenance",
        { optionId, option },
      );
    }
    check(
      item.synthetic ===
        (item.options || []).some((option) => option.synthetic),
      state.name,
      "room-advice-provenance",
      itemId,
    );
  }
  if (hotelId === primorskayaId) {
    check(
      advice.some(
        (item) =>
          item.characteristic_id === "sea_view" &&
          item.options?.some((option) => hasFifthFloor(option.scope)),
      ),
      state.name,
      "primorskaya-sea-view-fifth-floor-api",
      advice,
    );
  }
  checkedHotels.set(hotelId, advice);
  apiChecks.push({ hotelId, endpoint, advice });
  return advice;
}

const report = await captureGuestPages({
  base,
  artifacts,
  onPage: async (page, state) => {
    for (const item of state.forbiddenPhrases)
      failures.push({
        state: state.name,
        code: "guest-technical-language",
        ...item,
      });
    for (const item of state.repeatedPhrases)
      failures.push({
        state: state.name,
        code: "guest-repeated-sentence",
        ...item,
      });
    if (["search", "first-hotel", "primorskaya"].includes(state.page))
      for (const item of state.emptyBeachQuotes)
        failures.push({
          state: state.name,
          code: "guest-empty-beach-name",
          ...item,
        });
    check(
      state.scrollWidth <= state.viewportWidth + 1,
      state.name,
      "horizontal-overflow",
      { scrollWidth: state.scrollWidth, viewportWidth: state.viewportWidth },
    );
    if (state.page === "home")
      check(
        state.helper.leftOffset !== null &&
          Math.abs(state.helper.leftOffset) <= 1,
        state.name,
        "search-helper-text-alignment",
        state.helper,
      );
    if (await page.locator(".maplibregl-map:visible").count())
      check(
        state.innerText
          .replace(/\s/gu, "")
          .includes("©OpenStreetMapcontributors"),
        state.name,
        "visible-map-attribution",
        "Expected visible © OpenStreetMap contributors attribution",
      );
    if (["first-hotel", "primorskaya"].includes(state.page)) {
      const hotelId = new URL(state.url).pathname.split("/").at(-1);
      await checkRoomAdvice(page, state, hotelId);
      if (hotelId === primorskayaId)
        check(
          state.roomAdvice &&
            /Какой номер просить/iu.test(state.roomAdvice) &&
            /вид[^\n.!?]{0,30}на море|море из окна/iu.test(state.roomAdvice) &&
            /5\s*[-–]?\s*(?:го|й|м)?\s*этаж/iu.test(state.roomAdvice),
          state.name,
          "primorskaya-sea-view-fifth-floor-visible",
          state.roomAdvice,
        );
    }
  },
});

for (const state of report.states) {
  if (state.page === "search")
    check(
      report.requests.some((request) => {
        if (
          request.state !== state.name ||
          request.method !== "POST" ||
          new URL(request.url).pathname !== "/api/search"
        )
          return false;
        try {
          return JSON.parse(request.body).text === guestQuery;
        } catch {
          return false;
        }
      }),
      state.name,
      "live-search-query",
      guestQuery,
    );
  if (["first-hotel", "primorskaya"].includes(state.page)) {
    const hotelId = new URL(state.url).pathname.split("/").at(-1);
    check(
      report.requests.some((request) => {
        const url = new URL(request.url);
        return (
          request.state === state.name &&
          request.method === "GET" &&
          url.pathname === `/api/hotels/${hotelId}/room-advice` &&
          url.searchParams.get("month") === "9"
        );
      }),
      state.name,
      "live-room-advice-ui-request",
      `/api/hotels/${hotelId}/room-advice?month=9`,
    );
  }
}
for (const request of report.requests)
  check(
    ["GET", "HEAD", "OPTIONS"].includes(request.method) ||
      (request.method === "POST" &&
        (new URL(request.url).pathname === "/api/search" ||
          /^\/api\/hotels\/[^/]+\/evaluate$/u.test(
            new URL(request.url).pathname,
          ))),
    request.state,
    "unexpected-api-write",
    request,
  );
for (const error of report.errors)
  failures.push({ ...error, code: "browser-page-error" });
for (const response of report.responses)
  failures.push({ ...response, code: "http-error" });

const result = {
  status: failures.length ? "failed" : "passed",
  base,
  artifacts,
  states: report.states.length,
  apiChecks,
  failureCount: failures.length,
  failures,
};
await writeFile(
  resolve(artifacts, "smoke-result.json"),
  `${JSON.stringify(result, null, 2)}\n`,
);
console.log(JSON.stringify(result, null, 2));
if (failures.length) process.exitCode = 1;
