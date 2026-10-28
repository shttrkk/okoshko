import { record } from "../api";
import type { Fact, Side } from "../types";
import { PLACE_NAMES, tzHumanFact } from "./tz-facts";
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
export const TRAIT_NAMES: Record<string, string> = {
  ...PLACE_NAMES,
  morning_sun: "Утреннее солнце",
  evening_heat: "Вечернее солнце",
  railway_noise: "Железнодорожный шум",
  beach_surface: "Покрытие пляжа",
  accessible_route: "Доступность маршрута",
  green_nearby: "Зелень рядом",
};
/** Stable anchors shared by verdicts, cards and the map's return link. */
export const factCardId = (id: string) => `fact-card-${id}`;
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
export const roundedMinutes = (value: number) =>
  value > 0 ? Math.max(1, Math.floor(value + 0.5)) : 0;
export function minutes(v: unknown) {
  const n = num(v);
  if (n === null) return "—";
  return `≈ ${roundedMinutes(n)} мин`;
}
export function meters(v: unknown) {
  const n = num(v);
  if (n === null) return "—";
  return n >= 1000 ? `${fmt(n / 1000, 1)} км` : `${Math.round(n)} м`;
}
export const floorFrom = (v: unknown) =>
  num(v) === null ? "" : `с ${v}-го этажа`;
function range(low: unknown, high: unknown, digits = 0) {
  const a = num(low),
    b = num(high);
  return a === null
    ? b === null
      ? null
      : fmt(b, digits)
    : b === null || a === b
      ? fmt(a, digits)
      : `${fmt(a, digits)}–${fmt(b, digits)}`;
}
export type FacadeAzimuths = Partial<Record<Side, number>>;
/** A compass code is a sector, never an exact normal of a facade or window. */
export function sunsetAngle(fact: Fact, facadeAzimuths?: FacadeAzimuths) {
  const value = record(fact.value);
  const azimuth = num(value.azimuth_deg);
  if (fact.characteristic_id !== "sunset_view" || azimuth === null) return null;
  const evidenceNormals = [
    ...new Set(
      fact.evidence
        .filter(
          (evidence) =>
            evidence.kind === "calculation" || evidence.kind === "simulation",
        )
        .flatMap((evidence) => {
          const normal = num(evidence.inputs?.facade_azimuth_deg);
          return normal === null ? [] : [((normal % 360) + 360) % 360];
        }),
    ),
  ];
  if (evidenceNormals.length > 1) return null;
  const side = fact.scope.facade || value.facade;
  const normal =
    evidenceNormals[0] ??
    (typeof side === "string" ? num(facadeAzimuths?.[side as Side]) : null);
  return normal === null || normal === undefined
    ? null
    : Math.abs(((((azimuth - normal) % 360) + 540) % 360) - 180);
}
export function sunsetAspectText(fact: Fact, facadeAzimuths?: FacadeAzimuths) {
  if (record(fact.value).available !== true) return "";
  const angle = sunsetAngle(fact, facadeAzimuths);
  return angle === null
    ? "Точное направление окна неизвестно"
    : angle > 45
      ? "Закат виден сбоку"
      : "Закат перед выбранной стороной";
}
/** A boolean report is not a numerical outdoor-noise model. */
export function reportedQuiet(fact: Fact): boolean | null {
  const value = record(fact.value);
  if (
    fact.characteristic_id !== "quiet_night" ||
    noiseLabel[String(value.index)] ||
    [
      value.night_db_min,
      value.night_db_max,
      value.day_db_min,
      value.day_db_max,
    ].some((value) => num(value) !== null)
  )
    return null;
  return typeof value.available === "boolean" ? value.available : null;
}
const SURFACES: Record<string, string> = {
  sand: "песок",
  pebblestone: "галька",
  gravel: "гравий",
  fine_gravel: "мелкий гравий",
  shingle: "галька",
  rock: "камни",
  concrete: "бетон",
  ground: "грунт",
  grass: "трава",
};
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
export function humanFact(
  fact: Fact,
  facadeAzimuths?: FacadeAzimuths,
): string | null {
  const v = record(fact.value);
  const supplementary = tzHumanFact(fact);
  if (supplementary) return supplementary;
  if (fact.value === null || fact.status === "unknown") return null;
  const id = fact.characteristic_id;
  const month = num(fact.scope.month);
  switch (id) {
    case "morning_sun":
    case "evening_heat": {
      const duration = range(
        v.visible_minutes_min ?? v.visible_minutes,
        v.visible_minutes_max ?? v.visible_minutes,
      );
      const exposure = range(
        v.exposure_index_min ?? v.exposure_index,
        v.exposure_index_max ?? v.exposure_index,
        2,
      );
      if (duration === null && exposure === null) return null;
      return [
        month && `В ${MONTHS_IN[month - 1]}`,
        duration !== null &&
          `${id === "morning_sun" ? "утреннее" : "вечернее"} прямое солнце ${duration} мин`,
        duration === null &&
          exposure !== null &&
          "длительность прямого солнца не измерена",
        "по расчёту",
      ]
        .filter(Boolean)
        .join(" · ");
    }
    case "railway_noise": {
      const night = range(v.night_db_min, v.night_db_max);
      return night === null
        ? null
        : "Приближённый расчёт шума поездов · уровень внутри номера не измерен";
    }
    case "beach_surface": {
      const raw = typeof v.surface === "string" ? v.surface : null;
      const surface = raw
        ? raw
            .split(";")
            .map((part) => SURFACES[part.trim()] || part.trim())
            .join(", ")
        : null;
      return [
        surface ? `По карте: ${surface}` : "Покрытие пляжа не указано на карте",
        v.selection_kind === "beach_walk_destination"
          ? "пляж пешего маршрута"
          : v.selection_kind
            ? "ближайший пляж на карте"
            : null,
        v.selection_kind !== "beach_walk_destination" &&
          num(v.distance_m) !== null &&
          `${meters(v.distance_m)} по прямой`,
      ]
        .filter(Boolean)
        .join(" · ");
    }
    case "accessible_route": {
      return [
        v.wheelchair_suitable === true
          ? "По карте маршрут подходит для коляски"
          : v.wheelchair_suitable === false
            ? "В OSM есть явное ограничение проезда на коляске"
            : "Данных о проезде с коляской мало",
        v.has_steps === true
          ? "на маршруте отмечены лестницы"
          : v.has_steps === false
            ? "на рёбрах маршрута лестницы не отмечены"
            : "о лестницах нет данных",
      ]
        .filter(Boolean)
        .join(" · ");
    }
    case "green_nearby": {
      const kind =
        v.leisure === "garden"
          ? "сад"
          : v.leisure === "park"
            ? "парк"
            : "зелёная территория";
      return num(v.distance_m) === null
        ? null
        : `${kind[0].toUpperCase()}${kind.slice(1)} в ${meters(v.distance_m)} по прямой · вход и пеший путь не проверены`;
    }
    case "sunset_view": {
      const when = month ? `в ${MONTHS_IN[month - 1]}` : "";
      if (v.available === false) return `Не виден ${when}`.trim();
      if (v.available !== true) return null;
      return [
        `Виден ${when}`.trim(),
        floorFrom(v.min_floor),
        num(v.visible_minutes) !== null &&
          `видимость солнца последние ${minutes(v.visible_minutes)}`,
        sunsetAspectText(fact, facadeAzimuths),
      ]
        .filter(Boolean)
        .join(" · ");
    }
    case "sea_view":
    case "mountain_view": {
      const peaks = Array.isArray(v.peaks) ? v.peaks.join(", ") : "";
      if (v.available === false) return "Не виден";
      if (v.available !== true) return null;
      return ["Виден", floorFrom(v.min_floor), peaks]
        .filter(Boolean)
        .join(" · ");
    }
    case "quiet_night": {
      const report = reportedQuiet(fact);
      if (report !== null)
        return `${fact.synthetic ? "По ответу отеля · демо" : fact.status === "confirmed" ? "По словам отеля" : "Есть сообщение"}: ${report ? "ночью тихо" : "ночью не тихо"}. Внутри номера шум не измеряли`;
      const label = noiseLabel[String(v.index)];
      return label
        ? `По расчёту на улице ${label}. Внутри номера шум может отличаться`
        : null;
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
      const beachName =
        typeof v.beach_name === "string" &&
        !/\bosm-(way|node|relation)-\d+\b/i.test(v.beach_name)
          ? v.beach_name
              .replace(/^Пляж\s*/i, "")
              .replace(/["«»]/g, "")
              .trim()
          : "";
      return [
        `${minutes(v.minutes)} пешком, ${meters(v.distance_m)} ${beachName ? `до пляжа «${beachName}»` : "до ближайшего пляжа"}`,
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
          num(v.parking_distance_m) === null &&
            num(v.ev_distance_m) === null &&
            "парковка рядом не найдена в OSM",
          v.pedestrian_zone === true && "пешеходная зона",
        ]
          .filter(Boolean)
          .join(" · ")
          .replace(/^./, (c) => c.toUpperCase()) || null
      );
    case "nightlife": {
      const venues = num(v.venues_500m);
      if (venues === null) return null;
      return [
        `${venues} ${plural(venues, "заведение", "заведения", "заведений")} в ${meters(v.radius_m ?? 500)}`,
        num(v.bars_500m) !== null &&
          num(v.bars_500m)! > 0 &&
          `из них баров ${v.bars_500m}`,
        num(v.nearest_bar_m) !== null &&
          `ближайший бар в ${meters(v.nearest_bar_m)}`,
        num(v.promenade_distance_m) !== null &&
          `набережная в ${meters(v.promenade_distance_m)}`,
      ]
        .filter(Boolean)
        .join(" · ");
    }
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
    case "room_orientation": {
      const groups = Array.isArray(v.groups)
        ? v.groups
            .map((entry) => {
              const group = record(entry);
              const rooms = Array.isArray(group.rooms)
                ? group.rooms.map(String)
                : [];
              const roomNames =
                rooms.length > 3 &&
                rooms.every(
                  (room, index) =>
                    /^\d+$/.test(room) &&
                    (index === 0 ||
                      Number(room) === Number(rooms[index - 1]) + 1),
                )
                  ? `${rooms[0]}–${rooms.at(-1)}`
                  : rooms.join(", ");
              const traits = [
                group.balcony === true
                  ? "с балконом"
                  : group.balcony === false
                    ? "без балкона"
                    : null,
                group.sea_view === true
                  ? "вид на море"
                  : group.sea_view === false
                    ? "без вида на море"
                    : null,
                group.courtyard === true
                  ? "окна во двор"
                  : group.courtyard === false
                    ? "окна не во двор"
                    : null,
              ].filter(Boolean);
              return rooms.length
                ? `Номера ${roomNames}${traits.length ? `: ${traits.join(", ")}` : ""}`
                : null;
            })
            .filter(Boolean)
        : [];
      const absent = Array.isArray(v.absent_floors)
        ? v.absent_floors.filter(
            (floor) => typeof floor === "number" && Number.isInteger(floor),
          )
        : [];
      return (
        [
          ...groups,
          absent.length ? `На этажах ${absent.join(", ")} номеров нет` : null,
          !groups.length && v.balcony === true
            ? "Есть номера с балконом"
            : null,
        ]
          .filter(Boolean)
          .join(". ") || null
      );
    }
    case "connectivity": {
      const regional =
        !!v.quarter ||
        (typeof v.kind === "string" && /mobile|area|region|ookla/.test(v.kind));
      const wifi =
        v.wifi_in_rooms === true
          ? "Wi-Fi в номерах есть"
          : v.wifi_in_rooms === false
            ? "Wi-Fi в номерах нет"
            : v.wifi_reported === true
              ? "Wi-Fi есть по карте"
              : null;
      const fee =
        v.fee === "free" ? "бесплатно" : v.fee === "paid" ? "платно" : null;
      return num(v.download_mbps) !== null
        ? `${regional ? "Мобильный интернет в районе" : "Интернет по словам отеля"} ≈ ${fmt(v.download_mbps)} Мбит/с${regional ? " · скорость Wi-Fi отеля не измерена" : ""}`
        : wifi
          ? [wifi, fee, "скорость неизвестна"].filter(Boolean).join(" · ")
          : v.internet_access
            ? "Интернет указан на карте · тип и скорость неизвестны"
            : null;
    }
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

/** Plain-language names for claim methods and sources (ids stay visible). */
export const METHOD_NAMES: Record<string, string> = {
  "local_poi_family@1":
    "Площадка и больница по OSM; переходы на пути не рассчитывались",
  osm_property_internet_access_v1:
    "Тег интернета у того же объекта OSM; без измерения скорости",
  two_scale_dsm_los_mc_v1:
    "Линия видимости по рельефу и высотам зданий, 64 симуляции",
  astral_monthly_horizon_mc_v1:
    "Положение солнца по месяцам и закрытый горизонт, 64 симуляции",
  outdoor_noise_proxy_v1: "Расчёт уличного шума по дорогам и железной дороге",
  directed_graph_dijkstra_tobler_v1:
    "Пеший маршрут по графу OSM с учётом уклона",
  exact_quote_v1: "Дословная цитата с сайта отеля",
  "local_poi_lookup@2": "Поиск ближайших объектов OSM",
  "hotelier_answer@1": "Ответ отеля с цитатой",
  "simulated_hotelier_quote@1": "Демо-ответ отеля с цитатой",
  astral_morning_horizon_proxy_v1: "Расчёт утреннего солнца с учётом горизонта",
  astral_evening_exposure_proxy_v1:
    "Вечерняя солнечная экспозиция · не температура комнаты",
  outdoor_rail_noise_proxy_v1: "Приближённый расчёт уличного шума поездов",
  osm_beach_surface_v2: "Явный тег покрытия выбранного пляжа в OpenStreetMap",
  osm_route_accessibility_v1:
    "Явные теги доступности рёбер пешего маршрута OSM",
  osm_green_geometry_distance_v1:
    "Расстояние по прямой до картированного парка или сада OSM",
};
export const SOURCE_NAMES: Record<string, string> = {
  geo_engine: "Расчёт О!кно по OSM и Copernicus",
  osm_geofabrik: "OpenStreetMap",
  overture_buildings: "Overture Maps · здания",
  overture_places: "Overture Maps · места",
  copernicus_dem: "Copernicus DEM GLO-30",
  open_meteo_aq: "Open-Meteo · качество воздуха",
  open_meteo_archive: "Open-Meteo · архив погоды",
  open_meteo_climate: "Open-Meteo · климат",
  open_meteo_marine: "Open-Meteo · море",
  hotel_website: "Официальный сайт отеля",
  hotelier_simulator: "Ответ отеля · демо",
  wikimedia_commons: "Wikimedia Commons",
  wikidata: "Wikidata",
  ookla: "Ookla Open Data",
  protomaps: "Protomaps",
};
