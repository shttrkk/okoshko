import { useEffect, useRef, useState } from "react";
import { ArrowRight, Eye, MapPin } from "lucide-react";
import { api, record, useResource } from "../api";
import type {
  Criterion,
  Fact,
  Geo,
  HotelDetail,
  ParsedQuery,
  RoomScope,
  Scope,
  Side,
} from "../types";
import { MONTHS, MONTHS_IN, pct, SIDE_ORDER, SIDES } from "./facts";
import { ErrorState, FactValue, scopeText, Synthetic, VerdictList } from "./ui";
import { RoomProfileList } from "./RoomProfiles";
import { landmarkLabel, landmarkName } from "./tz-facts";
import { russianDateRange } from "./panel-guest-presentation";

export interface RoomSlice {
  facade: Side;
  floor: number;
  month: number;
  landmarkId?: string;
}
const roomTraits = new Set([
  "sea_view",
  "sunset_view",
  "mountain_view",
  "quiet_night",
  "soundproofing",
  "room_orientation",
  "morning_sun",
  "evening_heat",
  "railway_noise",
  "landmark_view",
]);
const integer = (value: unknown) =>
  typeof value === "number" &&
  Number.isInteger(value) &&
  value >= 1 &&
  value <= 100
    ? value
    : null;

function floorBoundary(detail: HotelDetail, geo: Geo) {
  const known = integer(detail.hotel.floors);
  const geometryCount = integer(geo.floors?.count);
  const modeled = integer(geo.floors?.modeled_count);
  // A hypothetical model level never establishes upper hotel floors.
  return {
    known,
    maximum: known
      ? Math.min(known, geometryCount || known, modeled || known)
      : 1,
  };
}
function initialSlice(
  facades: Side[],
  maximum: number,
  parsed: ParsedQuery | null,
  landmarkId?: string,
) {
  const params = new URLSearchParams(window.location.search);
  const side = params.get("room_side") as Side | null;
  const floor = Number(params.get("room_floor"));
  const month = Number(params.get("room_month"));
  return {
    landmarkId: params.get("room_landmark") || landmarkId,
    facade:
      side && facades.includes(side)
        ? side
        : parsed?.facade && facades.includes(parsed.facade)
          ? parsed.facade
          : facades[0],
    floor: Math.max(
      1,
      Math.min(maximum, integer(floor) || parsed?.min_floor || 1),
    ),
    month:
      Number.isInteger(month) && month >= 1 && month <= 12
        ? month
        : parsed?.month || 9,
  };
}
interface ScopeConflict {
  label: string;
  required: boolean;
  reason: string;
}
function scopeConflict(
  criterion: { scope?: (RoomScope & { landmark_id?: string | null }) | null },
  slice: RoomSlice,
) {
  const scope = criterion.scope;
  if (scope?.facade && scope.facade !== slice.facade)
    return `нужна сторона «${SIDES[scope.facade]}»`;
  if (scope?.floor_min && slice.floor < scope.floor_min)
    return `нужен этаж не ниже ${scope.floor_min}-го`;
  if (scope?.floor_max && slice.floor > scope.floor_max)
    return `нужен этаж не выше ${scope.floor_max}-го`;
  if (scope?.month && scope.month !== slice.month)
    return `условие относится к ${MONTHS_IN[scope.month - 1]}`;
  if (
    scope?.landmark_id &&
    slice.landmarkId &&
    scope.landmark_id !== slice.landmarkId
  )
    return `нужна цель «${landmarkLabel(scope.landmark_id)}»`;
  return null;
}
/** Intersect the requested room with the preview; never replace a hard scope. */
function sliceQuery(
  parsed: ParsedQuery | null,
  slice: RoomSlice | null,
  requestedScope?: RoomScope,
) {
  const conflicts: ScopeConflict[] = [];
  if (!parsed || !slice) return { query: null, conflicts };
  if (requestedScope) {
    const reason = scopeConflict({ scope: requestedScope }, slice);
    if (reason)
      conflicts.push({
        label: "Положение выбранной позиции",
        required: true,
        reason,
      });
  }
  if (parsed.facade && parsed.facade !== slice.facade)
    conflicts.push({
      label: "Сторона исходного запроса",
      required: true,
      reason: `нужна сторона «${SIDES[parsed.facade]}»`,
    });
  if (parsed.min_floor && slice.floor < parsed.min_floor)
    conflicts.push({
      label: "Этаж исходного запроса",
      required: true,
      reason: `нужен этаж не ниже ${parsed.min_floor}-го`,
    });
  for (const [criteria, required] of [
    [parsed.must, true],
    [parsed.nice, false],
    [parsed.no_data_must || [], true],
  ] as const) {
    for (const criterion of criteria) {
      const reason = scopeConflict(criterion, slice);
      if (reason) conflicts.push({ label: criterion.label, required, reason });
    }
  }
  // An empty intersection is displayed, never encoded as floor_min > floor_max.
  if (conflicts.length || (!parsed.must.length && !parsed.nice.length))
    return { query: null, conflicts };
  const intersect = (criterion: Criterion): Criterion => ({
    ...criterion,
    scope: {
      ...criterion.scope,
      type: criterion.scope?.room_type ? "room_type" : "floor_range",
      facade: slice.facade,
      floor_min: slice.floor,
      floor_max: slice.floor,
      month: slice.month,
      ...(criterion.characteristic_id === "landmark_view" && slice.landmarkId
        ? { landmark_id: slice.landmarkId }
        : {}),
    },
  });
  return {
    query: {
      ...parsed,
      facade: slice.facade,
      min_floor: slice.floor,
      month: slice.month,
      must: parsed.must.map(intersect),
      nice: parsed.nice.map(intersect),
      // Dates, unavailable hard requirements, route flags and thresholds stay intact.
    },
    conflicts,
  };
}
function inSlice(fact: Fact, slice: RoomSlice) {
  const value = record(fact.value);
  const side = fact.scope.facade || value.facade;
  const low =
    fact.scope.floor_min ?? (value.available === true ? value.min_floor : null);
  const high =
    fact.scope.floor_max ?? (value.available === true ? value.max_floor : null);
  const month = fact.scope.month ?? value.month;
  return (
    (!side || side === slice.facade) &&
    (typeof low !== "number" || low <= slice.floor) &&
    (typeof high !== "number" || high >= slice.floor) &&
    (typeof month !== "number" || month === slice.month) &&
    (!Array.isArray(value.months) || value.months.includes(slice.month)) &&
    (fact.characteristic_id !== "landmark_view" ||
      !slice.landmarkId ||
      (fact.scope.landmark_id || value.landmark_id) === slice.landmarkId)
  );
}
function sliceFact(facts: Fact[], id: string, slice: RoomSlice) {
  const own = facts.filter(
    (fact) =>
      fact.characteristic_id === id &&
      inSlice(fact, slice) &&
      // A confirmed/simulated "quiet" answer is not an outdoor dB model.
      (id !== "quiet_night" ||
        (typeof record(fact.value).night_db_min === "number" &&
          typeof record(fact.value).night_db_max === "number")),
  );
  // Hotel-wide view claims do not prove a window on the selected side/level.
  const scoped = roomTraits.has(id)
    ? own.filter(
        (fact) =>
          fact.scope.facade ||
          record(fact.value).facade ||
          (id === "landmark_view" && record(fact.value).available == null),
      )
    : own;
  return [...scoped].sort((a, b) => {
    const width = (fact: Fact) =>
      (fact.scope.floor_max || 100) - (fact.scope.floor_min || 1);
    return (
      width(a) - width(b) ||
      Number(b.status === "disputed") - Number(a.status === "disputed") ||
      b.confidence - a.confidence
    );
  })[0];
}
function ModelFact({
  id,
  title,
  facts,
  slice,
  geo,
  onEvidence,
}: {
  id: string;
  title: string;
  facts: Fact[];
  slice: RoomSlice;
  geo: Geo;
  onEvidence: (fact: Fact) => void;
}) {
  const fact = sliceFact(facts, id, slice);
  // Compact facts omit evidence. The hotelier parser cannot supply probability.
  const modelProbability = record(fact?.value).probability;
  const hasModelProbability =
    typeof modelProbability === "number" &&
    Number.isFinite(modelProbability) &&
    modelProbability >= 0 &&
    modelProbability <= 1;
  const facade = geo.facades?.find((entry) => entry.code === slice.facade);
  const probability = facade?.sea_probability_by_floor?.[slice.floor - 1];
  const expired =
    !!fact?.valid_until && Date.parse(fact.valid_until) <= Date.now();
  const hasValue =
    fact &&
    fact.value !== null &&
    !expired &&
    !["unknown", "stale", "disputed"].includes(fact.status);
  const seaProbability =
    id === "sea_view" &&
    hasValue &&
    hasModelProbability &&
    typeof probability === "number" &&
    Number.isFinite(probability)
      ? probability
      : null;
  return (
    <article
      className="room-lab-fact"
      data-testid={`room-lab-fact-${id}`}
      data-fact-id={fact?.id}
      data-model-value={JSON.stringify({
        value: fact?.value ?? null,
        seaProbability,
      })}
    >
      <h4>{title}</h4>
      {fact ? (
        <>
          <div className="room-lab-model-value">
            {seaProbability !== null ? (
              <>
                <FactValue fact={fact} />
                <details className="room-caveats-details">
                  <summary>Для дотошных</summary>Вероятность вида на море по
                  расчёту: {pct(seaProbability)}. Это чувствительность модели,
                  не надёжность данных.
                </details>
              </>
            ) : fact.status === "stale" || expired ? (
              "Данные устарели; для выбранной стороны и этажа нужна перепроверка."
            ) : fact.status === "disputed" ? (
              "Источники расходятся; сведения для выбранной стороны и этажа не подтверждены."
            ) : (
              <FactValue fact={fact} />
            )}
          </div>
          {id === "sea_view" && hasValue && !hasModelProbability && (
            <p className="muted small">
              Сообщение источника о виде для выбранной стороны и этажа.
            </p>
          )}
          <p className="muted small">
            Область источника: {scopeText(fact.scope)}.
          </p>
          {fact.synthetic && <Synthetic />}
          <button
            className="text-button small"
            data-testid={`room-lab-evidence-${id}`}
            onClick={() => onEvidence(fact)}
          >
            Основание <ArrowRight size={13} />
          </button>
        </>
      ) : (
        <p className="muted">
          Для выбранной стороны, этажа и месяца нет подходящего факта. Это не
          означает отсутствие свойства.
        </p>
      )}
      {id === "quiet_night" && (
        <p className="muted small">
          Расчёт уличного шума не определяет звук в комнате или качество
          шумоизоляции.
        </p>
      )}
      {id === "beach_walk" && (
        <p className="muted small">
          Маршрут относится к отелю; выбор фасада и этажа его не меняет.
        </p>
      )}
    </article>
  );
}
export function RoomLab({
  detail,
  geo,
  parsed,
  onSliceChange,
  onEvidence,
  onPanorama,
  mapActive,
  questionScope,
  roomLabel,
  roomIndex,
  requestedScope,
}: {
  detail: HotelDetail;
  geo: Geo;
  parsed: ParsedQuery | null;
  onSliceChange: (slice: RoomSlice | null) => void;
  onEvidence: (fact: Fact) => void;
  onPanorama: (side: string) => void;
  mapActive: boolean;
  roomLabel?: string;
  roomIndex?: number;
  requestedScope?: RoomScope;
  questionScope?: (
    characteristicId: string,
    slice: RoomSlice,
  ) => Scope | undefined;
}) {
  const facades = SIDE_ORDER.filter((side) =>
    geo.facades?.some((facade) => facade.code === side),
  ) as Side[];
  const boundary = floorBoundary(detail, geo);
  const landmarks = [
    ...new Map(
      detail.facts
        .filter((fact) => fact.characteristic_id === "landmark_view")
        .map((fact) => [
          String(
            fact.scope.landmark_id || record(fact.value).landmark_id || "",
          ),
          fact,
        ]),
    ).entries(),
  ].filter(([id]) => !!id);
  const [slice, setSlice] = useState(() =>
    initialSlice(
      facades,
      boundary.maximum,
      parsed,
      [...(parsed?.must || []), ...(parsed?.nice || [])].find(
        (criterion) => criterion.characteristic_id === "landmark_view",
      )?.scope?.landmark_id || undefined,
    ),
  );
  const [open, setOpen] = useState(
    () =>
      new URLSearchParams(window.location.search).has("room_side") ||
      new URLSearchParams(window.location.search).has("room_request"),
  );
  const sliceChange = useRef(onSliceChange);
  sliceChange.current = onSliceChange;
  const valid = !!slice.facade && facades.includes(slice.facade);
  useEffect(() => {
    sliceChange.current(open && valid ? slice : null);
  }, [open, valid, slice]);
  const plan = sliceQuery(parsed, valid ? slice : null, requestedScope);
  const key = JSON.stringify([detail.hotel.id, plan.query]);
  const evaluation = useResource(
    () =>
      open && plan.query
        ? api
            .hotel(detail.hotel.id, plan.query)
            .then((value) => ({ key, detail: value }))
        : Promise.resolve(null),
    [open, key],
  );
  const evaluated =
    evaluation.data?.key === key ? evaluation.data.detail : null;
  const unavailable = parsed?.no_data || [];
  const unavailableMust = parsed?.no_data_must || [];
  const differentSeason =
    !!parsed &&
    (slice.month !== parsed.month ||
      [parsed.check_in, parsed.check_out].some(
        (date) => date && Number(date.slice(5, 7)) !== slice.month,
      ));
  const shared = new URLSearchParams({
    room_side: slice.facade || "",
    room_floor: String(slice.floor),
    room_month: String(slice.month),
  });
  if (roomIndex !== undefined) shared.set("room_request", String(roomIndex));
  if (slice.landmarkId) shared.set("room_landmark", slice.landmarkId);
  const extra = [
    ["mountain_view", "Горы"],
    ["sunset_view", "Закат"],
    ["morning_sun", "Утреннее солнце"],
    ["evening_heat", "Вечернее солнце"],
    ["railway_noise", "Железнодорожный шум"],
    ["climate", "Климат и вода"],
  ].filter(([id]) =>
    detail.facts.some((fact) => fact.characteristic_id === id),
  );
  return (
    <section
      className="section-block room-lab"
      id="room-lab"
      data-testid="room-lab"
      data-open={open}
    >
      <details
        open={open}
        onToggle={(event) => setOpen(event.currentTarget.open)}
      >
        <summary>
          <strong>Проверить окно</strong>
          <span>Выберите сторону, этаж и месяц</span>
        </summary>
        <div className="room-lab-body">
          <p className="room-lab-disclaimer">
            Проверяем условия для выбранной стороны и этажа. Реальный номер,
            расположение окон и наличие мест подтвердит отель.
          </p>
          {roomLabel && (
            <p className="room-lab-person" data-testid="room-lab-person">
              Пожелания: <strong>{roomLabel}</strong>. Общие условия и даты
              сохранены.
            </p>
          )}
          {!valid ? (
            <p className="muted">
              Не хватает данных о сторонах здания. Проверка пока недоступна.
            </p>
          ) : (
            <>
              <div className="room-lab-controls">
                <label>
                  Сторона здания
                  <select
                    aria-label="Сторона здания"
                    data-testid="room-lab-facade"
                    value={slice.facade}
                    onChange={(event) =>
                      setSlice({ ...slice, facade: event.target.value as Side })
                    }
                  >
                    {facades.map((side) => (
                      <option value={side} key={side}>
                        {SIDES[side]}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  {boundary.known ? "Этаж" : "Условный уровень"}
                  <select
                    aria-label="Этаж для проверки"
                    data-testid="room-lab-floor"
                    value={slice.floor}
                    onChange={(event) =>
                      setSlice({ ...slice, floor: Number(event.target.value) })
                    }
                  >
                    {Array.from({ length: boundary.maximum }, (_, index) => (
                      <option key={index + 1} value={index + 1}>
                        {index + 1}
                        {boundary.known ? "-й этаж" : "-й уровень"}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Месяц
                  <select
                    aria-label="Месяц для проверки"
                    data-testid="room-lab-month"
                    value={slice.month}
                    onChange={(event) =>
                      setSlice({ ...slice, month: Number(event.target.value) })
                    }
                  >
                    {MONTHS.map((name, index) => (
                      <option key={name} value={index + 1}>
                        {name}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              <p className="muted small" data-testid="room-lab-floor-note">
                {boundary.known
                  ? `Известная этажность: ${boundary.known}. Для проверки доступны этажи до ${boundary.maximum}-го; свободные номера уточнит отель.`
                  : "Этажность нужно уточнить у отеля. Пока можно проверить только нижний этаж по модели."}
              </p>
              {parsed && (
                <p
                  className="muted small"
                  data-testid="room-lab-original-dates"
                >
                  Исходный месяц запроса: {MONTHS[parsed.month - 1]}. Даты
                  поездки: {russianDateRange(parsed.check_in, parsed.check_out)}
                  . Они не меняются.
                </p>
              )}
              {differentSeason && (
                <p
                  className="room-lab-warning"
                  data-testid="room-lab-season-preview"
                >
                  Отдельный сезонный предпросмотр для{" "}
                  {MONTHS_IN[slice.month - 1]}. Он не оценивает условия на
                  исходные даты поездки и не изменяет их.
                </p>
              )}
              <div className="room-lab-actions">
                <button
                  className="text-button small"
                  data-testid="room-lab-map"
                  aria-pressed={mapActive}
                  onClick={() => {
                    onSliceChange(slice);
                    document
                      .getElementById(`hotel-map-${detail.hotel.id}`)
                      ?.scrollIntoView({ behavior: "smooth", block: "center" });
                  }}
                >
                  <MapPin size={14} /> Показать на карте
                </button>
                <button
                  className="text-button small"
                  onClick={() => onPanorama(slice.facade)}
                >
                  <Eye size={14} /> Панорама стороны
                </button>
                <a
                  className="text-button small"
                  data-testid="room-lab-share"
                  href={`/hotel/${encodeURIComponent(detail.hotel.id)}?${shared}`}
                >
                  Ссылка на выбранные условия <ArrowRight size={13} />
                </a>
              </div>
              <div
                className="room-lab-model-facts"
                data-testid="room-lab-model-facts"
              >
                <ModelFact
                  id="sea_view"
                  title="Море"
                  facts={detail.facts}
                  slice={slice}
                  geo={geo}
                  onEvidence={onEvidence}
                />
                <ModelFact
                  id="quiet_night"
                  title="Шум ночью, по расчёту"
                  facts={detail.facts}
                  slice={slice}
                  geo={geo}
                  onEvidence={onEvidence}
                />
                <ModelFact
                  id="beach_walk"
                  title="Путь до пляжа"
                  facts={detail.facts}
                  slice={slice}
                  geo={geo}
                  onEvidence={onEvidence}
                />
              </div>
              {landmarks.length > 0 && (
                <div
                  className="room-lab-landmark"
                  data-testid="room-lab-landmark"
                >
                  <label>
                    Достопримечательность
                    <select
                      aria-label="Достопримечательность"
                      data-testid="room-lab-landmark-select"
                      value={slice.landmarkId || landmarks[0][0]}
                      onChange={(event) =>
                        setSlice({ ...slice, landmarkId: event.target.value })
                      }
                    >
                      {landmarks.map(([id, fact]) => (
                        <option key={id} value={id}>
                          {landmarkName(fact)}
                        </option>
                      ))}
                    </select>
                  </label>
                  <ModelFact
                    id="landmark_view"
                    title="Вид на выбранную достопримечательность"
                    facts={detail.facts}
                    slice={{
                      ...slice,
                      landmarkId: slice.landmarkId || landmarks[0][0],
                    }}
                    geo={geo}
                    onEvidence={onEvidence}
                  />
                  <button
                    className="text-button small"
                    data-testid="room-lab-landmark-map"
                    onClick={() => {
                      const selected = {
                        ...slice,
                        landmarkId: slice.landmarkId || landmarks[0][0],
                      };
                      setSlice(selected);
                      onSliceChange(selected);
                      document
                        .getElementById(`hotel-map-${detail.hotel.id}`)
                        ?.scrollIntoView({
                          behavior: "smooth",
                          block: "center",
                        });
                    }}
                  >
                    <MapPin size={14} /> Цель и лучи на карте
                  </button>
                </div>
              )}
              {extra.length > 0 && (
                <details className="room-lab-extra">
                  <summary>Ещё условия</summary>
                  <div className="room-lab-model-facts">
                    {extra.map(([id, title]) => (
                      <ModelFact
                        key={id}
                        id={id}
                        title={title}
                        facts={detail.facts}
                        slice={slice}
                        geo={geo}
                        onEvidence={onEvidence}
                      />
                    ))}
                  </div>
                </details>
              )}
              <RoomProfileList
                hotelId={detail.hotel.id}
                unknownFloors={!boundary.known}
                scope={{
                  facade: slice.facade,
                  floor_min: slice.floor,
                  floor_max: slice.floor,
                  month: slice.month,
                }}
                open={open}
              />
              <div className="room-lab-query" data-testid="room-lab-query">
                <h3>Ваши пожелания для выбранного номера</h3>
                {!parsed ||
                (!parsed.must.length &&
                  !parsed.nice.length &&
                  !unavailable.length &&
                  !unavailableMust.length) ? (
                  <p className="muted">
                    Условия поиска не заданы. Выше показаны только доступные
                    сведения о выбранной стороне и этаже; совпадение с запросом
                    не оценивается.
                  </p>
                ) : (
                  <>
                    <p className="muted small">
                      Проверяем ваши пожелания для этой стороны, этажа и месяца.
                      Условия дороги до пляжа относятся ко всему отелю.
                    </p>
                    {plan.conflicts.length > 0 && (
                      <div
                        className="room-lab-warning"
                        data-testid="room-lab-scope-conflict"
                      >
                        <strong>
                          {plan.conflicts.some((conflict) => conflict.required)
                            ? "Выбранный номер не соответствует обязательным условиям."
                            : "Выбранный номер не соответствует некоторым пожеланиям."}
                        </strong>
                        <ul>
                          {plan.conflicts.map((conflict, index) => (
                            <li key={index}>
                              {conflict.label}: {conflict.reason}
                              {conflict.required
                                ? " — обязательно"
                                : " — желательно"}
                              .
                            </li>
                          ))}
                        </ul>
                        <p>
                          Пожелания сохранены. Поиск с новыми условиями не
                          отправлен; факты выбранной стороны можно изучать
                          отдельно.
                        </p>
                      </div>
                    )}
                    {unavailable.length + unavailableMust.length > 0 && (
                      <div
                        className="room-lab-unavailable"
                        data-testid="room-lab-unavailable"
                      >
                        <strong>Данных региона не хватает</strong>
                        <ul>
                          {[
                            ...new Map(
                              [...unavailable, ...unavailableMust].map(
                                (criterion) => [
                                  `${criterion.characteristic_id}:${criterion.field}:${criterion.op}:${JSON.stringify(criterion.value)}`,
                                  criterion,
                                ],
                              ),
                            ).values(),
                          ].map((criterion, index) => (
                            <li key={index}>
                              {criterion.label}
                              {unavailableMust.some(
                                (must) =>
                                  must.characteristic_id ===
                                    criterion.characteristic_id &&
                                  must.field === criterion.field,
                              )
                                ? " — обязательное условие не подтверждено"
                                : " — проверить нельзя"}
                              .
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {plan.query && evaluation.loading && (
                      <p className="muted" role="status">
                        Проверяем выбранную сторону и этаж…
                      </p>
                    )}
                    <ErrorState
                      error={evaluation.error}
                      retry={evaluation.reload}
                    />
                    {evaluated && (
                      <div
                        data-testid="room-lab-verdicts"
                        data-room-facade={slice.facade}
                        data-room-floor={slice.floor}
                        data-room-month={slice.month}
                      >
                        <VerdictList
                          hotelId={detail.hotel.id}
                          verdicts={evaluated.verdicts}
                          questionScope={(verdict) =>
                            questionScope?.(verdict.characteristic_id, slice)
                          }
                          onEvidence={(verdict) => {
                            const fact = evaluated.facts.find((entry) =>
                              verdict.fact_ids.includes(entry.id),
                            );
                            if (fact) onEvidence(fact);
                          }}
                        />
                      </div>
                    )}
                  </>
                )}
              </div>
            </>
          )}
        </div>
      </details>
    </section>
  );
}
