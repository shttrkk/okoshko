import type { Criterion, Fact, Verdict } from "../types";

export const DEMO_REPLY = "Ответ отеля · демо";
export const DEMO_CALCULATION = "Демо-расчёт";
export const DEMO_EXPLANATION =
  "Ответ отеля · демо: в прототипе ответ симулирован";
export const DEMO_BANNER =
  "В прототипе ответы нескольких отелей симулированы — так выглядит выдача после ответа отеля";

export function reliability(confidence: number, synthetic = false) {
  return synthetic
    ? "Демо"
    : confidence >= 0.8
      ? "надёжно"
      : confidence >= 0.6
        ? "примерно"
        : "мало данных";
}
export function realHotelier(verdict: Verdict) {
  return (
    verdict.status === "yes" &&
    !verdict.synthetic &&
    !verdict.disputed &&
    verdict.hotelier_confirmed === true
  );
}
export function verdictLabel(verdict: Verdict) {
  if (verdict.disputed) return "Есть противоречие";
  if (verdict.synthetic)
    return isDemoAnswer(verdict) ? DEMO_REPLY : DEMO_CALCULATION;
  if (realHotelier(verdict)) return "Подтвердил отель";
  if (
    verdict.characteristic_id === "connectivity" &&
    verdict.status === "partial" &&
    /скорость (?:неизвестна|не измерена|не проверена)/i.test(verdict.text)
  )
    return "мало данных";
  if (verdict.basis === "open_data" && verdict.status === "yes")
    return "по карте";
  return {
    yes: "по расчёту",
    partial: "примерно",
    no: "не подходит",
    unknown: "мало данных",
  }[verdict.status];
}
export function verdictSymbol(verdict: Verdict) {
  return verdict.disputed
    ? "!"
    : verdict.status === "yes"
      ? realHotelier(verdict)
        ? "✓"
        : "≈"
      : verdict.symbol;
}
export function verdictTone(verdict: Verdict) {
  return verdict.disputed
    ? "disputed"
    : verdict.status === "yes" && !realHotelier(verdict)
      ? "partial"
      : verdict.status;
}
export function factLabel(fact: Fact) {
  if (fact.status === "disputed") return "Есть противоречие";
  if (fact.synthetic) return factDemoLabel(fact);
  if (fact.status === "confirmed") return "Подтвердил отель";
  if (fact.status === "unknown" || fact.value === null) return "Мало данных";
  if (fact.status === "stale") return "Нужна перепроверка";
  return fact.evidence.some((e) => e.kind === "quote")
    ? "Сайт отеля · цитата"
    : fact.evidence.some((e) => e.kind === "open_data")
      ? "По открытым данным"
      : "По расчёту";
}
/** Presentation only: source records, original quotes and API values stay intact. */
export function guestText(text: string, synthetic = false) {
  if (synthetic)
    text = text.replace(/^Синтетические демонстрационные данные:\s*/i, "");
  if (
    /СИМУЛЯЦИЯ|Синтетический ответ|hotelier_simulator|synthetic-demo|фикстур|demo truth|Вымышленный ответ|Confidence относится|Синтетические данные/i.test(
      text,
    )
  )
    return synthetic
      ? DEMO_EXPLANATION
      : "Подробности источника доступны в данных расчёта.";
  return text.replace(
    /(до\s+)?(?:пляж[ауе]?\s*)?[«"]?OSM\s+osm-(?:way|node|relation)-\d+[»"]?/gi,
    (_, to: string) => (to ? "до ближайшего пляжа" : "ближайший пляж"),
  );
}
export function criterionLabel(c: Criterion) {
  if (typeof c.value !== "number")
    return c.value === false && !/(^|\s)(без|нет|не)\s/i.test(c.label)
      ? `${c.label}: нет`
      : c.label;
  const comparison = `${c.op === "lte" ? "≤" : c.op === "gte" ? "≥" : "="} ${c.value.toLocaleString("ru-RU")}`;
  return /[≤≥=]\s*-?\d+(?:[.,]\d+)?/.test(c.label)
    ? c.label.replace(/[≤≥=]\s*-?\d+(?:[.,]\d+)?/g, comparison)
    : `${c.label} ${comparison}${criterionUnit(c) ? ` ${criterionUnit(c)}` : ""}`;
}
export function criterionBaseLabel(c: Criterion) {
  return c.label.split(/[≤≥=]/)[0].trim();
}
export function criterionUnit(c: Criterion) {
  return c.field === "minutes"
    ? "мин пешком"
    : c.field === "water_c"
      ? "°C"
      : c.field?.endsWith("_m")
        ? "м"
        : c.field?.endsWith("_mbps")
          ? "Мбит/с"
          : c.field === "max_weight_kg"
            ? "кг"
            : c.field === "venues_500m"
              ? "заведений в 500 м"
              : "";
}

export function developerDisplay(value: unknown, synthetic = false): unknown {
  if (typeof value === "string") return guestText(value, synthetic);
  if (Array.isArray(value))
    return value.map((item) => developerDisplay(item, synthetic));
  if (value && typeof value === "object")
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [
        key,
        developerDisplay(item, synthetic),
      ]),
    );
  return value;
}

export function isDemoAnswer(verdict: Verdict) {
  return (
    !!verdict.synthetic &&
    !["calculation", "open_data"].includes(verdict.basis || "unknown")
  );
}
export function factDemoLabel(fact: Fact) {
  if (
    fact.evidence.some(
      (e) =>
        e.kind === "hotelier" || e.source_ids.includes("hotelier_simulator"),
    )
  )
    return DEMO_REPLY;
  if (
    fact.evidence.some((e) =>
      ["calculation", "open_data", "simulation"].includes(e.kind),
    )
  )
    return DEMO_CALCULATION;
  // Compact facts defer their source. A neutral badge does not invent it.
  return "Демо";
}
export function factDemoExplanation(fact: Fact) {
  const label = factDemoLabel(fact);
  return label === DEMO_REPLY
    ? DEMO_EXPLANATION
    : label === DEMO_CALCULATION
      ? "Демо-расчёт: в прототипе показан пример пересчёта."
      : "Демо: подробности происхождения загружаются из источника.";
}

/** Only the known legacy demo metadata prefix is omitted; quote content is verbatim. */
export function guestQuote(quote: string, synthetic = false) {
  return synthetic
    ? quote.replace(
        /^(?:СИМУЛЯЦИЯ[.:]?\s*|Синтетический ответ для демонстрации:\s*)/i,
        "",
      )
    : quote;
}

/** Short display copy only; original verdicts and evidence stay in the proof. */
export function humanExplanation(
  text: string,
  synthetic = false,
  sentences = 2,
) {
  const first = guestText(text.split(";")[0], synthetic).replace(
    /^(?:Да, но учитывайте условия: |Предварительно: |Не подтверждено: )/,
    "",
  );
  return first
    .replace(
      /Ночью снаружи\s*[≈~]?\s*\d+(?:[.,]\d+)?(?:[–—-]\d+(?:[.,]\d+)?)?\s*дБ по расч[её]ту\s*[—–-]\s*/gi,
      "По расчёту снаружи ",
    )
    .replace(
      /Модельный прокси наружного (?:железнодорожного )?шума ночью:[^.]+\.?/gi,
      "Приблизительный расчёт уличного шума.",
    )
    .replace(/\s+по модели последних[^;.]*?(?:над горизонтом|$)/gi, "")
    .replace(
      /(?:[·,]\s*)?(?:ночью\s*)?[≈~]?\s*\d+(?:[.,]\d+)?(?:[–—-]\d+(?:[.,]\d+)?)?\s*дБ/gi,
      "",
    )
    .replace(
      /\s*[·,]\s*(?:уровень в дБ не указан|угол[^;.]*|ориентировочный угол[^;.]*)/gi,
      "",
    )
    .replace(
      /(?:^|[·,]\s*)индекс (?:вечерней солнечной )?экспозиции[^;.]*|\d+(?:[.,]\d+)?(?:[–—-]\d+)?°[^;.]*/gi,
      "",
    )
    .replace(/условный уровень 1/gi, "этажность нужно уточнить")
    .replace(/Данных нет(?:\s+Данных нет)+/gi, "Данных нет")
    .replace(/\s*[·,]\s*$/, "")
    .split(/(?<=[.!?])\s+(?=[А-ЯЁA-Z])/u)
    .slice(0, sentences)
    .join(" ");
}
export function relevantLimitation(text: string, characteristicId: string) {
  if (/больниц/i.test(text) && characteristicId !== "family_nearby")
    return false;
  if (
    /наличи[ея] (?:номеров|окон)|окна? на фасад/i.test(text) &&
    ![
      "sea_view",
      "sunset_view",
      "mountain_view",
      "room_orientation",
      "morning_sun",
      "evening_heat",
      "quiet_night",
      "soundproofing",
      "railway_noise",
    ].includes(characteristicId)
  )
    return false;
  return true;
}

export function verdictCaveat(verdict: Verdict) {
  let unrelated = false;
  return (verdict.caveat || "")
    .split(";")
    .map((part) => part.trim())
    .filter((part) => {
      if (!relevantLimitation(part, verdict.characteristic_id)) {
        unrelated = true;
        return false;
      }
      if (unrelated && /^это вопрос отельеру/i.test(part)) {
        unrelated = false;
        return false;
      }
      unrelated = false;
      return !!part;
    })
    .map((part) => guestText(part, !!verdict.synthetic))
    .filter((part, index, all) => all.indexOf(part) === index)
    .join("; ");
}

/** Nearby routes and places remain information from the map, not hotel promises. */
export const MAP_REFERENCE_IDS = new Set([
  "beach_walk",
  "beach_surface",
  "night_pharmacy",
  "family_nearby",
  "nightlife",
  "green_nearby",
]);
export function russianDateRange(start?: string | null, end?: string | null) {
  const format = (date: string) =>
    new Intl.DateTimeFormat("ru-RU", {
      day: "numeric",
      month: "long",
      year: "numeric",
      timeZone: "UTC",
    })
      .format(new Date(date))
      .replace(/\s*г\.$/, " года");
  if (!start && !end) return "Предварительный выбор · даты ещё не указаны";
  if (!start || !end) return format((start || end)!);
  const a = new Date(start),
    b = new Date(end);
  return a.getUTCFullYear() === b.getUTCFullYear() &&
    a.getUTCMonth() === b.getUTCMonth()
    ? `${a.getUTCDate()}–${format(end)}`
    : `${format(start)} — ${format(end)}`;
}
