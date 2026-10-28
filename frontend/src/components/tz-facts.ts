import { record } from "../api";
import type { Fact } from "../types";

/** Presentation only: these labels never infer a value or alter evidence. */
export const PLACE_NAMES: Record<string, string> = {
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
};
export const isPlaceTrait = (id: string) => id in PLACE_NAMES;
const n = (value: unknown) =>
  typeof value === "number" && Number.isFinite(value) ? value : null;
const f = (value: unknown, digits = 0) =>
  n(value)?.toLocaleString("ru-RU", { maximumFractionDigits: digits }) ?? "—";
const distance = (value: unknown) =>
  n(value) === null
    ? null
    : Number(value) >= 1000
      ? `≈ ${f(Number(value) / 1000, 1)} км`
      : `≈ ${f(value)} м`;
const minutes = (value: unknown) =>
  n(value) === null
    ? null
    : `≈ ${Math.max(Number(value) > 0 ? 1 : 0, Math.floor(Number(value) + 0.5))} мин`;
const join = (parts: unknown[]) => parts.filter(Boolean).join(" · ");
const sentence = (text: string) =>
  text ? text[0].toUpperCase() + text.slice(1) : text;
const safeName = (value: unknown) =>
  typeof value === "string" &&
  !/^(?:OSM\s*)?osm-(?:way|node|relation)-|^[a-z]+[-_]\d+$/i.test(value)
    ? value
    : null;
export function landmarkLabel(id: string) {
  return (
    (
      {
        sea_terminal: "Морской вокзал — верх крыши без шпиля",
        bolshoy: "Ледовый дворец «Большой»",
        winter_theatre: "Зимний театр",
        fisht: "Стадион «Фишт»",
      } as Record<string, string>
    )[id] || "Выбранная достопримечательность"
  );
}
export function landmarkName(fact: Fact) {
  const value = record(fact.value);
  return (
    safeName(value.name) ||
    landmarkLabel(String(fact.scope.landmark_id || value.landmark_id))
  );
}
export function landmarkHeightSource(source: unknown) {
  if (typeof source !== "string" || !source)
    return "Высота не подтверждена; видимость не рассчитана";
  if (source.includes("osm_building_levels"))
    return "Высота приблизительно по этажности на карте, с учётом рельефа";
  if (source.includes("osm_height"))
    return "Высота по отметке на карте, с учётом рельефа";
  if (source.includes("wikidata"))
    return "Высота по Wikidata, с учётом рельефа";
  if (source.includes("dsm")) return "Верх контура с учётом рельефа";
  return "Высота модели из сохранённого источника";
}
export function isSupplementalFact(fact: Fact) {
  const v = record(fact.value);
  return (
    isPlaceTrait(fact.characteristic_id) ||
    (fact.characteristic_id === "family_nearby" &&
      [
        "walk_playground",
        "walk_hospital",
        "walk_children_hospital",
        "walk_promenade",
      ].some((key) => key in v)) ||
    (fact.characteristic_id === "car_access" &&
      ("walk_parking" in v || "walk_ev" in v)) ||
    (fact.characteristic_id === "climate" &&
      ("sunny_days" in v || "rainy_days" in v)) ||
    fact.characteristic_id === "building_history"
  );
}
/** Missing graph coverage is unknown. available=false means outside the near threshold. */
function route(label: string, input: unknown) {
  const v = record(input);
  if (n(v.minutes) === null) return `${label}: пеший путь неизвестен`;
  return join([
    `${label}: ${minutes(v.minutes)} пешком`,
    distance(v.distance_m),
    v.available === false &&
      n(v.threshold_minutes) !== null &&
      `дальше ${f(v.threshold_minutes)} мин`,
  ]);
}
function routeDetails(input: unknown) {
  const v = record(input);
  if (n(v.minutes) === null) return null;
  return join([
    safeName(v.name),
    n(v.ascent_m) !== null && `подъём ≈ ${f(v.ascent_m)} м`,
    v.has_steps === true && "есть лестницы",
    v.has_steps === false && "на карте лестницы не отмечены",
    Array.isArray(v.crossings) &&
      v.crossings.length > 0 &&
      `пересечений с дорогами и путями: ${v.crossings.length}`,
    v.emergency === true && "в данных отмечена экстренная помощь",
  ]);
}
const ROUTE_FIELDS: Record<string, string> = {
  walk_playground: "Детская площадка",
  walk_hospital: "Медицинская помощь",
  walk_children_hospital: "Больница с детским профилем",
  walk_promenade: "Набережная или пешеходная улица",
  walk_parking: "Парковка",
  walk_ev: "Зарядка электромобиля",
  center_route: "До ориентира центра",
  station_route: "Станция электрички",
  bus_stop_route: "Остановка",
  dog_park: "Площадка для выгула",
  ordinary_park_fallback: "Обычный парк, не площадка для выгула",
  shop: "Магазин ночью",
  food: "Еда ночью",
};
export function tzUnknownReason(id: string, value: unknown = null) {
  const v = record(value);
  switch (id) {
    case "photo_features":
      return "Нет явных сведений о балконах, террасе и бассейне у этого здания; по внешнему виду фото вывод не делаем.";
    case "events_nearby":
      return "Нет доступного открытого календаря на даты поездки. Это не означает, что мероприятий нет.";
    case "mobile_internet":
      return "Нет мобильных тестов Ookla для точки отеля. Скорость и поколение связи неизвестны.";
    case "building_history":
      return "История и возраст здания не подтверждены точным источником.";
    case "hotel_parking":
      return "Связь парковки с отелем не подтверждена. Наличие и условия уточнит отель.";
    case "landmark_view":
      return v.landmark_id === "fisht"
        ? "Высота «Фишта» не подтверждена, поэтому видимость не рассчитана."
        : "Для выбранной цели, стороны и этажа не хватает данных о видимости.";
    case "construction_nearby":
      return "Нет свежей проверки текущих работ. Архивные отметки не подтверждают стройку сейчас.";
    default:
      return "Открытых данных недостаточно. Неизвестное не означает отсутствие.";
  }
}
export function tzHumanFact(fact: Fact): string | null {
  if (!isSupplementalFact(fact)) return null;
  const v = record(fact.value),
    id = fact.characteristic_id;
  if (fact.status === "stale")
    return "? Сведения устарели, нужна перепроверка источника.";
  if (fact.status === "disputed")
    return "! Источники расходятся, значение требует уточнения.";
  if (fact.value === null || fact.status === "unknown")
    return `? ${tzUnknownReason(id, { ...v, landmark_id: fact.scope.landmark_id || v.landmark_id })}`;
  switch (id) {
    case "airport_noise":
      return join([
        v.in_corridor === true
          ? "Под геометрическим коридором взлёта и посадки"
          : v.in_corridor === false
            ? "Вне рассчитанного коридора взлёта и посадки"
            : "Положение относительно коридора неизвестно",
        n(v.runway_distance_m) !== null &&
          `до полосы ${distance(v.runway_distance_m)}`,
      ]);
    case "airport_transfer": {
      const km = v.car_distance_km ?? v.airport_car_distance_km;
      return join([
        n(v.transfers) === 0
          ? "По карте есть связность без пересадок"
          : n(v.transfers) !== null
            ? `По карте пересадок: ${f(v.transfers)}`
            : "Пересадки на общественном транспорте неизвестны",
        n(km) !== null && `на машине ≈ ${f(km, 1)} км`,
        n(v.scheduled_minutes) !== null &&
          `по расписанию ${minutes(v.scheduled_minutes)}`,
      ]);
    }
    case "car_arrival":
      return join([
        n(v.road_distance_m) !== null
          ? `До проезжей дороги ${distance(v.road_distance_m)}`
          : "Расстояние до проезжей дороги неизвестно",
        v.available === false && "дорога дальше порога близкого подъезда",
        v.private_parking === true && "своя парковка указана в источнике",
      ]);
    case "climate":
    case "climate_days":
      return join([
        n(v.sunny_days) !== null &&
          `солнечных дней ≈ ${f(v.sunny_days)} в месяц`,
        n(v.rainy_days) !== null && `дождливых ≈ ${f(v.rainy_days)}`,
        n(v.water_c) !== null &&
          `вода ${Number(v.water_c) >= 22 ? "достаточно тёплая для купания" : Number(v.water_c) >= 18 ? "прохладная" : "холодная"}`,
        safeName(v.water_comfort),
      ]);
    case "construction_nearby":
      return n(v.current_eligible_count) !== null &&
        Number(v.current_eligible_count) > 0
        ? `Свежих отметок работ рядом: ${f(v.current_eligible_count)}; активность и шум не измерены`
        : `? Текущие работы неизвестны${n(v.mapped_count) !== null && Number(v.mapped_count) > 0 ? ` · архивных отметок: ${f(v.mapped_count)}` : ""}`;
    case "dog_walk":
      return v.dog_park
        ? route("Площадка для выгула", v.dog_park)
        : join([
            "Путь до площадки для выгула неизвестен",
            v.ordinary_park_fallback &&
              route(
                "Обычный парк, не площадка для выгула",
                v.ordinary_park_fallback,
              ),
          ]);
    case "landmark_view":
      return join([
        `${landmarkName(fact)}: ${v.available === true ? "верх объекта виден по расчёту" : v.available === false ? "верх объекта не виден с выбранной стороны и этажа" : "видимость неизвестна"}`,
        n(v.distance_m) !== null && distance(v.distance_m),
      ]);
    case "mobile_masts":
      return n(v.mapped_count) !== null
        ? `${f(v.mapped_count)} мачт связи на карте в радиусе ${distance(v.radius_m) || "неуказанного расстояния"} · качество связи неизвестно`
        : "? Нет сведений о мачтах связи";
    case "night_open":
      return join([route("Магазин ночью", v.shop), route("Еда ночью", v.food)]);
    case "veterinary_nearby":
      return route("Ветклиника", v);
    case "pet_shop_nearby":
      return route("Зоомагазин", v);
    case "location_access":
      return join([
        safeName(v.district_name),
        v.in_central_sochi === true
          ? "в Центральном районе Сочи"
          : v.in_central_sochi === false
            ? "вне Центрального района Сочи"
            : null,
        v.center_route &&
          route(
            `До ориентира «${safeName(record(v.zone_center).name) || "центр района"}»`,
            v.center_route,
          ),
        route("Станция электрички", v.station_route),
        route("Остановка", v.bus_stop_route),
      ]);
    case "mobile_internet":
      return n(v.download_mbps) !== null
        ? `Мобильный интернет в районе ≈ ${f(v.download_mbps)} Мбит/с · по тестам Ookla, не Wi-Fi отеля`
        : `? ${tzUnknownReason(id, v)}`;
    case "photo_features": {
      const features = [
        ["balcony", "Балконы"],
        ["terrace", "Терраса"],
        ["pool", "Бассейн"],
      ].flatMap(([key, label]) =>
        typeof v[key] === "boolean"
          ? [
              `${label}: ${v[key] ? "есть по источнику" : "в источнике явно указано «нет»"}`,
            ]
          : [],
      );
      return features.length ? join(features) : `? ${tzUnknownReason(id, v)}`;
    }
    case "events_nearby":
      return n(v.event_count) !== null
        ? `Событий в доступном календаре: ${f(v.event_count)}`
        : `? ${tzUnknownReason(id, v)}`;
    case "hotel_parking":
      return join([
        v.private_parking === true
          ? "Своя парковка указана в источнике"
          : v.private_parking === false
            ? "Своей парковки нет по сообщению источника"
            : "? Своя парковка неизвестна",
        n(v.fee_rub_per_day) !== null &&
          (Number(v.fee_rub_per_day) === 0
            ? "без доплаты"
            : `${f(v.fee_rub_per_day)} ₽ в сутки`),
        v.reservation_required === true
          ? "нужно бронировать"
          : v.reservation_required === false
            ? "бронь не требуется"
            : null,
        v.ev_charging === true
          ? "есть зарядка электромобиля"
          : v.ev_charging === false
            ? "зарядки нет по источнику"
            : null,
      ]);
    case "family_nearby":
      return join(
        Object.entries(ROUTE_FIELDS)
          .filter(([key]) => key.startsWith("walk_") && key in v)
          .map(([key, label]) => route(label, v[key])),
      );
    case "car_access":
      return join([
        "walk_parking" in v && route("Парковка", v.walk_parking),
        "walk_ev" in v && route("Зарядка электромобиля", v.walk_ev),
      ]);
    case "building_history":
      return v.history_information == null &&
        v.historic_designation == null &&
        !v.explicit_building_date &&
        !v.inception &&
        !v.architecture
        ? `? ${tzUnknownReason(id, v)}`
        : join([
            v.explicit_building_date
              ? `Дата постройки по источнику: ${String(v.explicit_building_date)}`
              : v.inception
                ? `В источнике указана дата ${String(v.inception)}; дата постройки не подтверждена`
                : null,
            v.historic_designation === true
              ? "есть явная историческая или охранная отметка"
              : v.historic_designation === false
                ? "источник явно отрицает исторический статус"
                : "исторический статус неизвестен",
            safeName(v.architecture) && `стиль по источнику: ${v.architecture}`,
          ]);
    default:
      return null;
  }
}
/** Supplementary observations keep their own facts; no confidence/evidence merging. */
export function tzSummary(id: string, facts: Fact[], month: number) {
  const own = facts.filter(
    (fact) =>
      isSupplementalFact(fact) &&
      (!fact.scope.month || fact.scope.month === month),
  );
  if (!own.length) return null;
  if (id === "landmark_view") {
    const targets = [
      ...new Set(
        own.map(
          (fact) => fact.scope.landmark_id || record(fact.value).landmark_id,
        ),
      ),
    ];
    const summaries = targets.map((target) => {
      const group = own.filter(
        (fact) =>
          (fact.scope.landmark_id || record(fact.value).landmark_id) === target,
      );
      const yes = group.filter(
        (fact) =>
          !["unknown", "stale", "disputed"].includes(fact.status) &&
          record(fact.value).available === true,
      );
      const best = [...yes].sort(
        (a, b) => (a.scope.floor_min || 1) - (b.scope.floor_min || 1),
      )[0];
      if (best)
        return `${landmarkName(best)}: по расчёту виден верх объекта${best.scope.facade ? ` · ${SIDE_NAMES[best.scope.facade]}` : ""}${best.scope.floor_min ? ` · с ${best.scope.floor_min}-го этажа` : ""}`;
      const first = group[0];
      return group.every(
        (fact) =>
          record(fact.value).available === false &&
          !["unknown", "stale", "disputed"].includes(fact.status),
      )
        ? `${landmarkName(first)}: на рассчитанных сторонах и этажах верх объекта не виден`
        : `${landmarkName(first)}: видимость неизвестна`;
    });
    return summaries.join(". ");
  }
  return [
    ...new Set(own.map(tzHumanFact).filter((text): text is string => !!text)),
  ]
    .map(sentence)
    .join(". ");
}
const SIDE_NAMES: Record<string, string> = {
  N: "север",
  NE: "северо-восток",
  E: "восток",
  SE: "юго-восток",
  S: "юг",
  SW: "юго-запад",
  W: "запад",
  NW: "северо-запад",
};
export function tzDetail(fact: Fact) {
  const v = record(fact.value),
    id = fact.characteristic_id;
  if (id === "airport_noise")
    return join([
      "Коридор и расстояние рассчитаны по карте. Уровень шума, частота и ночные рейсы неизвестны",
      Array.isArray(v.facing_facades) &&
        v.facing_facades.length > 0 &&
        `к полосе обращены стороны: ${v.facing_facades.map((side) => SIDE_NAMES[String(side)] || "неизвестная сторона").join(", ")}`,
    ]);
  if (id === "airport_transfer") {
    const refs = Array.isArray(v.routes)
      ? [
          ...new Set(
            v.routes.map((item) => safeName(record(item).ref)).filter(Boolean),
          ),
        ]
      : [];
    const reason = (
      {
        hotel_stops_absent_from_relations:
          "Ближайшие остановки не связаны с маршрутами в доступной карте",
        no_mapped_zero_or_one_transfer_join:
          "В доступной карте не удалось связать остановки аэропорта и отеля без пересадок или с одной пересадкой",
        hotel_stop_directed_walk_not_established:
          "Пеший подход от остановки к отелю по графу не установлен",
        hotel_ordered_members_missing:
          "В карте не хватает порядка остановок, чтобы проверить направление поездки",
        hotel_stop_walk_exceeds_threshold:
          "Пеший подход от остановки дальше принятого порога",
        hotel_stop_snap_missing_or_walk_disconnected:
          "Остановка и отель не соединены доступным пешим графом",
      } as Record<string, string>
    )[String(v.primary_unknown_reason)];
    return join([
      "Связность по карте не заменяет расписание",
      reason,
      refs.length > 0 &&
        `маршруты по карте: ${refs.map((ref) => String(ref).replace(/=>/g, "→")).join(", ")}`,
      v.nearest_station &&
        route("Ближайшая станция электрички", v.nearest_station),
      safeName(record(v.nearest_station).name),
      v.nearest_bus_stop && route("Ближайшая остановка", v.nearest_bus_stop),
      v.scheduled_minutes == null && "время в пути по расписанию неизвестно",
      v.night_service == null
        ? "ночное движение неизвестно"
        : v.night_service === true
          ? "ночное движение указано источником"
          : "по источнику ночного движения нет",
      "Неописанные подходы к входам и текущая дорожная обстановка не учтены",
    ]);
  }
  if (id === "car_arrival")
    return "Близость дороги к входу или контуру здания не подтверждает открытый въезд и возможность подъехать к двери.";
  if (id === "climate_days" || id === "climate")
    return join([
      n(v.sunny_day_threshold_hours) !== null &&
        `Солнечный день: не менее ${f(v.sunny_day_threshold_hours)} ч солнца`,
      n(v.rainy_day_threshold_mm) !== null &&
        `дождливый день: осадки от ${f(v.rainy_day_threshold_mm)} мм`,
      v.wet_month_proxy === true && "по принятому порогу месяц дождливый",
      n(v.complete_sun_years) !== null &&
        `полных лет по солнцу: ${f(v.complete_sun_years)}`,
      n(v.complete_rain_years) !== null &&
        `по дождям: ${f(v.complete_rain_years)}`,
      Array.isArray(v.period) &&
        `период погоды: ${v.period.map(String).join(" — ")}`,
      Array.isArray(v.water_period) &&
        `период воды: ${v.water_period.map(String).join(" — ")}`,
      n(v.water_sample_days) !== null &&
        `дней наблюдения воды за этот месяц: ${f(v.water_sample_days)}`,
      "Это средние значения, а не прогноз на даты поездки",
    ]);
  if (id === "construction_nearby") return tzUnknownReason(id, v);
  if (id === "mobile_masts")
    return "Количество картированных мачт не доказывает покрытие, скорость или наличие 4G/5G. Ноль на карте не означает отсутствие мачт.";
  if (id === "mobile_internet")
    return join([
      n(v.upload_mbps) !== null && `отдача ≈ ${f(v.upload_mbps)} Мбит/с`,
      n(v.latency_ms) !== null && `задержка ≈ ${f(v.latency_ms)} мс`,
      n(v.tests) !== null && `тестов: ${f(v.tests)}`,
      n(v.devices) !== null && `устройств: ${f(v.devices)}`,
      safeName(v.quarter),
      "Тесты района не подтверждают Wi-Fi отеля или работу сети в номере; поколение 4G/5G неизвестно",
    ]);
  if (id === "landmark_view")
    return "Проверяется верх моделируемого контура объекта. Вид зависит от стороны и этажа; расчёт не подтверждает наличие окна или номера.";
  if (id === "photo_features")
    return join([
      ...[
        ["balcony", "Балконы"],
        ["terrace", "Терраса"],
        ["pool", "Бассейн"],
      ].flatMap(([key, label]) =>
        v[key] == null ? [`${label}: неизвестно`] : [],
      ),
      "Используются только явные теги связанного здания и описания проверенных фото; изображения не распознаются",
    ]);
  if (id === "events_nearby") return tzUnknownReason(id, v);
  if (id === "hotel_parking")
    return join([
      v.private_parking == null && tzUnknownReason(id, v),
      v.fee_rub_per_day == null && "стоимость неизвестна",
      v.reservation_required == null && "нужно ли бронировать — неизвестно",
      v.ev_charging == null && "зарядка на территории не подтверждена",
    ]);
  if (id === "building_history")
    return "Дата начала работы объекта может означать открытие отеля. Наличие записи об истории не доказывает старинное здание или охранный статус.";
  const routes =
    id === "veterinary_nearby" || id === "pet_shop_nearby"
      ? [routeDetails(v)]
      : Object.entries(ROUTE_FIELDS)
          .filter(([key]) => key in v)
          .map(([key, label]) => {
            const text = routeDetails(v[key]);
            return text ? `${label}: ${text}` : null;
          });
  return join([
    ...routes,
    id === "night_open" &&
      `Открытие по указанным на карте часам в ${typeof v.open_at === "string" ? v.open_at : "02:00"}; неизвестные часы не означают «закрыто»`,
    id === "dog_walk" && "Правила выгула в обычном парке неизвестны",
    id === "location_access" &&
      record(v.metro).available === false &&
      "По источнику на конец 2024 года в Сочи нет метро; показаны электричка и остановка",
    "Пешие минуты рассчитаны по графу; неописанные подходы к входам в них не входят",
  ]);
}
