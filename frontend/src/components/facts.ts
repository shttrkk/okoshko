import { record } from "../api";
import type { Fact } from "../types";
export const MONTHS = [
  "январь",
  "февраль",
  "март",
  "апрель",
  "май",
  "июнь",
  "июль",
  "август",
  "сентябрь",
  "октябрь",
  "ноябрь",
  "декабрь",
];
export const MONTHS_IN = [
  "январе",
  "феврале",
  "марте",
  "апреле",
  "мае",
  "июне",
  "июле",
  "августе",
  "сентябре",
  "октябре",
  "ноябре",
  "декабре",
];
export const MONTHS_SHORT = [
  "янв",
  "фев",
  "мар",
  "апр",
  "май",
  "июн",
  "июл",
  "авг",
  "сен",
  "окт",
  "ноя",
  "дек",
];
export const SIDE_ORDER = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
export const SIDES: Record<string, string> = {
  N: "север",
  NE: "северо-восток",
  E: "восток",
  SE: "юго-восток",
  S: "юг",
  SW: "юго-запад",
  W: "запад",
  NW: "северо-запад",
};
export const SIDES_SHORT: Record<string, string> = {
  N: "С",
  NE: "СВ",
  E: "В",
  SE: "ЮВ",
  S: "Ю",
  SW: "ЮЗ",
  W: "З",
  NW: "СЗ",
};
const num = (v: unknown) =>
  typeof v === "number" && Number.isFinite(v) ? v : null;
/** Russian number formatting without false precision. */
export function fmt(v: unknown, digits = 0) {
  const n = num(v);
  return n === null
    ? "—"
    : n.toLocaleString("ru", {
        maximumFractionDigits: digits,
        minimumFractionDigits: 0,
      });
}
export const pct = (v: unknown) =>
  num(v) === null ? "—" : `${Math.round((v as number) * 100)}%`;
export function minutes(v: unknown) {
  const n = num(v);
  if (n === null) return "—";
  return n < 1 ? "меньше минуты" : `≈ ${Math.round(n)} мин`;
}
export function meters(v: unknown) {
  const n = num(v);
  if (n === null) return "—";
  return n >= 1000 ? `${fmt(n / 1000, 1)} км` : `${Math.round(n)} м`;
}
export const floorFrom = (v: unknown) =>
  num(v) === null ? "" : `с ${v}-го этажа`;
function aqiLabel(aqi: number) {
  if (aqi <= 20) return "хорошее";
  if (aqi <= 40) return "удовлетворительное";
  if (aqi <= 60) return "умеренное";
  if (aqi <= 80) return "плохое";
  if (aqi <= 100) return "очень плохое";
  return "крайне плохое";
}
export const noiseLabel: Record<string, string> = {
  quiet: "тихо",
  moderate: "умеренно",
  loud: "шумно",
  noisy: "шумно",
};
const plural = (n: number, one: string, few: string, many: string) => {
  const m10 = n % 10,
    m100 = n % 100;
  return m10 === 1 && m100 !== 11
    ? one
    : m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)
      ? few
      : many;
};
/**
 * Human-readable sentence for a fact value, or null when the generic
 * key/value rendering should be used.
 */
export function humanFact(fact: Fact): string | null {
  const v = record(fact.value);
  if (fact.value === null || fact.status === "unknown") return null;
  const id = fact.characteristic_id;
  const month = num(fact.scope.month);
  switch (id) {
    case "sunset_view": {
      const when = month ? `в ${MONTHS_IN[month - 1]}` : "";
      if (v.available === false)
        return (
          `Не виден ${when}`.trim() + ` · вероятность ${pct(v.probability)}`
        );
      return [
        `Виден ${when}`.trim(),
        floorFrom(v.min_floor),
        num(v.visible_minutes) !== null &&
          `солнце в окне последние ${minutes(v.visible_minutes)}`,
        `вероятность ${pct(v.probability)}`,
      ]
        .filter(Boolean)
        .join(" · ");
    }
    case "sea_view":
    case "mountain_view": {
      const peaks = Array.isArray(v.peaks) ? v.peaks.join(", ") : "";
      if (v.available === false)
        return `Не виден · вероятность ${pct(v.probability)}`;
      if (v.available !== true) return null;
      return [
        "Виден",
        floorFrom(v.min_floor),
        peaks,
        num(v.probability) !== null && `вероятность ${pct(v.probability)}`,
      ]
        .filter(Boolean)
        .join(" · ");
    }
    case "quiet_night": {
      const label = noiseLabel[String(v.index)] || "";
      const db =
        num(v.night_db_min) !== null
          ? `ночью ${fmt(v.night_db_min)}–${fmt(v.night_db_max)} дБ`
          : "";
      return (
        [label && label[0].toUpperCase() + label.slice(1), db]
          .filter(Boolean)
          .join(" · ") || null
      );
    }
    case "climate":
      return [
        `Воздух ${fmt(v.air_c)}°`,
        num(v.water_c) !== null && `вода ${fmt(v.water_c)}°`,
        num(v.sunshine_hours) !== null &&
          `солнце ${fmt(v.sunshine_hours)} ч в месяц`,
      ]
        .filter(Boolean)
        .join(" · ");
    case "beach_walk": {
      if (v.available === false) return "Пешего маршрута до пляжа не нашлось";
      const crossings = Array.isArray(v.crossings) ? v.crossings.length : 0;
      return [
        `${minutes(v.minutes)} пешком, ${meters(v.distance_m)}${
          v.beach_name
            ? ` до «${String(v.beach_name)
                .replace(/^Пляж\s*/i, "")
                .replace(/"/g, "")}»`
            : ""
        }`,
        num(v.return_minutes) !== null &&
          `обратно ${minutes(v.return_minutes)}${num(v.return_ascent_m) ? `, подъём ${fmt(v.return_ascent_m)} м` : ""}`,
        v.has_steps === true && "есть лестницы",
        crossings > 0 &&
          `${crossings} ${plural(crossings, "переход", "перехода", "переходов")} через дорогу`,
      ]
        .filter(Boolean)
        .join(" · ");
    }
    case "night_pharmacy":
      if (v.available === false) return "Круглосуточной аптеки рядом нет";
      return `Аптека ${v.opening_hours === "24/7" ? "круглосуточно" : "ночью"} в ${meters(v.distance_m)}`;
    case "family_nearby":
      return (
        [
          num(v.playground_distance_m) !== null &&
            `детская площадка в ${meters(v.playground_distance_m)}`,
          num(v.hospital_distance_m) !== null &&
            `больница в ${meters(v.hospital_distance_m)}`,
        ]
          .filter(Boolean)
          .join(" · ")
          .replace(/^./, (c) => c.toUpperCase()) || null
      );
    case "car_access":
      return (
        [
          num(v.parking_distance_m) !== null &&
            `парковка в ${meters(v.parking_distance_m)}`,
          num(v.ev_distance_m) !== null &&
            `зарядка для электромобиля в ${meters(v.ev_distance_m)}`,
          v.pedestrian_zone === true && "пешеходная зона",
        ]
          .filter(Boolean)
          .join(" · ")
          .replace(/^./, (c) => c.toUpperCase()) || null
      );
    case "pets": {
      if (v.available === false) return "С питомцами нельзя";
      if (v.requires_confirmation) return "Можно по согласованию с отелем";
      const species = Array.isArray(v.species)
        ? v.species
            .map((s) => ({ cat: "кошкой", dog: "собакой" })[String(s)] || s)
            .join(" или ")
        : "питомцем";
      return [
        `Можно с ${species}`,
        num(v.max_weight_kg) !== null && `до ${fmt(v.max_weight_kg)} кг`,
        num(v.fee_rub_night) !== null &&
          (v.fee_rub_night
            ? `${fmt(v.fee_rub_night)} ₽ за ночь`
            : "без доплаты"),
      ]
        .filter(Boolean)
        .join(" · ");
    }
    case "air_quality": {
      const aqi = num(v.european_aqi);
      if (aqi === null) return null;
      return `${aqiLabel(aqi)[0].toUpperCase()}${aqiLabel(aqi).slice(1)} · индекс AQI ${fmt(aqi)}${num(v.pm2_5) !== null ? ` · PM2,5 ${fmt(v.pm2_5, 1)} мкг/м³` : ""}`;
    }
    case "room_orientation":
      return v.balcony === true ? "Есть номера с балконом" : null;
    case "connectivity":
      return num(v.download_mbps) !== null
        ? `Интернет ≈ ${fmt(v.download_mbps)} Мбит/с`
        : null;
    case "nearby_photos": {
      const n = Array.isArray(v.photos) ? v.photos.length : 0;
      return n
        ? `${n} ${plural(n, "фото", "фото", "фото")} окрестностей с геометкой`
        : null;
    }
    default:
      return null;
  }
}
