// Explicit UI fixtures for structured choices and statuses. They never write facts.
export const suggestionCriterion = {
  characteristic_id: "beach_walk",
  label: "До пляжа ≤ 10 мин пешком",
  field: "minutes",
  op: "lte",
  value: 10,
  weight: 1,
};
const quietCriterion = {
  characteristic_id: "quiet_night",
  label: "Тишина ночью",
  field: "available",
  op: "eq",
  value: true,
  weight: 1,
  scope: {
    type: "floor_range",
    facade: "W",
    floor_min: 3,
    floor_max: 5,
    month: 9,
  },
};
export function queryFixture(seed, mode, edited) {
  const common = {
    ...structuredClone(seed.parsed),
    text: mode,
    must: [],
    nice: [],
    rooms: [],
    no_data: [],
    no_data_must: [],
    unmapped: [],
    suggestions: [],
    clarifications: [],
    check_in: "2027-09-14",
    check_out: "2027-09-21",
    month: 9,
  };
  if (mode === "choice-global") {
    common.unmapped = ["недалеко", "центр"];
    common.suggestions = [
      {
        label: "Добавить: до моря ≤ 10 мин",
        criterion: suggestionCriterion,
        required: true,
        fragment: "недалеко",
      },
    ];
  }
  if (mode === "choice-room") {
    common.rooms = [
      {
        label: "Тёще",
        scope: { facade: "W", floor_min: 3, floor_max: 5, month: 9 },
        must: [],
        nice: [],
        unmapped: ["потише", "двор"],
        suggestions: [
          {
            label: "Добавить: тишина ночью",
            criterion: quietCriterion,
            required: true,
            fragment: "потише",
          },
        ],
      },
      {
        label: "Нам",
        scope: { month: 9 },
        must: [],
        nice: [],
        unmapped: ["необычный интерьер"],
        suggestions: [],
      },
    ];
  }
  const parsed = edited || common;
  const verdict = (id, status, text) => ({
    characteristic_id: id,
    label:
      id === "nightlife"
        ? "В гуще событий ≥ 30 заведений"
        : id === "beach_walk"
          ? "До пляжа без лестниц"
          : id === "connectivity"
            ? "Связь для работы"
            : "Шумоизоляция и окна",
    required: true,
    status,
    symbol: status === "no" ? "✗" : status === "partial" ? "≈" : "?",
    text,
    caveat:
      id === "beach_walk"
        ? "Расчёт видимости не подтверждает наличие номеров или окон на фасаде; это вопрос отельеру; На маршруте отмечены лестницы"
        : id === "nightlife"
          ? "Детский профиль больницы не проверен; Карта может быть неполной"
          : null,
    confidence: 0.65,
    fact_ids: [],
    evidence_refs: [],
    synthetic: true,
    hotelier_confirmed: false,
    basis: "calculation",
  });
  const prototypeHit = {
    ...structuredClone(seed.results[0]),
    verdicts: [],
    room_plan:
      parsed.rooms?.map((room) => ({
        label: room.label,
        scope: room.scope,
        recommendation: "Уточните пожелания этой позиции у отеля.",
        verdicts: [],
        status: "unknown",
        profile: null,
        caveats: [],
      })) || [],
    summary: "Для этого выбора ещё нужно уточнить данные у отеля.",
    disappointments: [],
    why_not_higher: [],
    must_total: parsed.must.length,
    must_satisfied: 0,
    group: "unknown",
  };
  let results = [prototypeHit];
  if (mode === "known-statuses") {
    parsed.must = [
      suggestionCriterion,
      {
        characteristic_id: "nightlife",
        label: "В гуще событий ≥ 30 заведений",
        field: "venues_500m",
        op: "gte",
        value: 30,
        weight: 1,
      },
      {
        characteristic_id: "connectivity",
        label: "Связь для работы",
        field: "download_mbps",
        op: "gte",
        value: 30,
        weight: 1,
      },
      {
        characteristic_id: "soundproofing",
        label: "Шумоизоляция и окна",
        field: "available",
        op: "eq",
        value: true,
        weight: 1,
      },
    ];
    results = [
      {
        ...prototypeHit,
        hotel: {
          ...prototypeHit.hotel,
          name: "Маршрут · проверка демо-расчёта",
        },
        verdicts: [
          verdict(
            "beach_walk",
            "no",
            "Не подходит: на маршруте лестницы, подъём обратно 37 м",
          ),
        ],
        must_total: 1,
      },
      {
        ...prototypeHit,
        hotel: {
          ...structuredClone(seed.results[1].hotel),
          name: "Окружение · проверка демо-расчёта",
        },
        verdicts: [
          verdict(
            "nightlife",
            "no",
            "По карте мало: 9 заведений из 30; карта может быть неполной",
          ),
        ],
        must_total: 1,
      },
      {
        ...prototypeHit,
        hotel: {
          ...structuredClone(seed.results[2].hotel),
          name: "Wi-Fi · проверка демо-данных",
        },
        verdicts: [
          verdict(
            "connectivity",
            "partial",
            "Wi-Fi указан, скорость не измерена",
          ),
          verdict("soundproofing", "unknown", "Отель пока не ответил"),
        ],
        must_total: 2,
      },
    ];
  }
  return { ...structuredClone(seed), parsed, results, total: results.length };
}
