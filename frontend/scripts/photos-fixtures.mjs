// Explicit browser-only fixtures. They never alter the catalogue or API state.
export const fixtureAssetHost = "hotel-photos.example";
export const fixtureHotelId = "photos-fixture-room";
export const noPhotoHotelId = "photos-fixture-empty";
export function photosFixtures(seedDetail, seedSearch) {
  const hotel = {
    ...seedDetail.hotel,
    id: fixtureHotelId,
    name: "Гостиница браузерной проверки",
    image: null,
    website: `https://${fixtureAssetHost}/rooms`,
    tags: { fixture: true },
    price_rub: null,
  };
  const emptyHotel = {
    ...hotel,
    id: noPhotoHotelId,
    name: "Гостиница без фото",
  };
  const scope = { facade: "W", floor_min: 3, floor_max: 5, month: 9 };
  const profile = {
    id: "fixture-profile",
    scope,
    title: "Положение для проверки",
    description: "",
    clauses: [],
    caveats: [],
    conditional: false,
  };
  const criterion = (characteristic_id) => ({
    characteristic_id,
    label: characteristic_id === "quiet_night" ? "Тише ночью" : "Закат из окна",
    field: "available",
    op: "eq",
    value: true,
    weight: 1,
  });
  const verdict = (status, characteristic_id, required = true) => ({
    characteristic_id,
    label: criterion(characteristic_id).label,
    status,
    required,
    symbol: { yes: "✓", partial: "≈", no: "✗", unknown: "?" }[status],
    text: "Браузерная проверка условия",
    confidence: 0.6,
    fact_ids: [],
    evidence_refs: [],
  });
  const parsed = {
    ...seedSearch.parsed,
    text: "Браузерная проверка фотографий",
    must: [criterion("quiet_night"), criterion("sunset_view")],
    nice: [],
    no_data: [],
    no_data_must: [],
    rooms: [],
  };
  const hit = {
    ...seedSearch.results[0],
    hotel,
    room_profile: profile,
    room_plan: [],
    room_recommendation: "Для вашего запроса: запад, этажи 3–5.",
    must_satisfied: 3,
    must_total: 3,
    verdicts: [
      verdict("yes", "sunset_view"),
      verdict("partial", "quiet_night"),
      verdict("partial", "sea_view"),
    ],
    group: "all",
    why_not_higher: [],
    disappointments: [],
    summary: "",
  };
  const search = {
    ...seedSearch,
    parsed,
    results: [
      hit,
      {
        ...hit,
        hotel: emptyHotel,
        group: "unknown",
        must_total: 5,
        verdicts: [verdict("no", "sunset_view")],
        room_plan: [
          {
            label: "Второе пожелание",
            scope,
            status: "unknown",
            recommendation: "Нужно уточнить",
            verdicts: [
              verdict("partial", "quiet_night"),
              verdict("unknown", "soundproofing"),
            ],
            caveats: [],
          },
        ],
      },
    ],
    total: 2,
  };
  const detail = {
    ...seedDetail,
    hotel,
    room_profile: profile,
    room_plan: [],
    room_recommendation: hit.room_recommendation,
    verdicts: hit.verdicts,
    disappointments: [],
  };
  const option = (floor_min, floor_max, facade = "W") => ({
    scope: { facade, floor_min, floor_max, month: 9 },
    fact_ids: ["fixture-fact"],
    evidence_refs: ["fixture-evidence"],
    status: "partial",
    synthetic: true,
  });
  const advice = (options) => [
    {
      id: "fixture-quiet",
      characteristic_id: "quiet_night",
      title: "Меньше уличного шума",
      text: "Сравнение сторон по проверочным данным.",
      options,
      fact_ids: ["fixture-fact"],
      evidence_refs: ["fixture-evidence"],
      status: "partial",
      synthetic: true,
      caveats: [],
    },
  ];
  const room = (n) => ({
    kind: "room",
    url: `https://${fixtureAssetHost}/room-${n}.webp`,
    page_url: `https://${fixtureAssetHost}/rooms`,
    local: false,
    license: null,
    author: null,
    license_url: null,
  });
  const photos = {
    [fixtureHotelId]: [
      {
        kind: "official_site",
        url: `https://${fixtureAssetHost}/cover.webp`,
        page_url: hotel.website,
        local: false,
      },
      ...Array.from({ length: 10 }, (_, n) => room(n)),
      ...[
        "javascript:alert(1)",
        "data:image/svg+xml,<svg/>",
        "https://yandex.ru/forbidden-room.webp",
        "https://images.mapillary.com/forbidden-room.webp",
        "https://booking.com/forbidden-room.webp",
      ].map((url) => ({ ...room(99), url })),
    ],
    [noPhotoHotelId]: [],
  };
  return {
    hotel,
    emptyHotel,
    parsed,
    profile,
    search,
    detail,
    photos,
    option,
    advice,
  };
}
