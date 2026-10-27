import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import {
  ArrowRight,
  ArrowUpRight,
  Compass,
  Eye,
  MapPin,
  ShieldCheck,
  TriangleAlert,
} from "lucide-react";
import { api, record, useResource } from "../api";
import { useApp } from "../state";
import { GeoModal, TravelMap } from "../components/Map";
import { FactsPanel } from "../components/FactsPanel";
import {
  factCardId,
  SIDES,
  TRAIT_NAMES,
  type FacadeAzimuths,
} from "../components/facts";
import { PanoramaModal } from "../components/Panorama";
import { RoomLab, type RoomSlice } from "../components/RoomLab";
import { GroupPlan } from "../components/GroupPlan";
import { RoomAdviceSection } from "../components/RoomAdvice";
import { RoomDescription } from "../components/RoomProfiles";
import { personalRoomQuery, roomLabParams } from "../components/group";
import {
  AskButton,
  BackLink,
  CompareButton,
  Empty,
  ErrorState,
  EvidenceModal,
  HotelVisual,
  Loading,
  scopeText,
  Synthetic,
  VerdictList,
} from "../components/ui";
import type { Fact, Hotel, Scope, SearchHit, Side, Verdict } from "../types";
import "../night-details.css";
const conflictStatus: Record<string, string> = {
  asked: "Спросили отель",
  open: "Открыт",
  auto_resolved: "Разобрано автоматически",
  resolved: "Разрешён",
};
const roomQuestionTraits = new Set([
  "sea_view",
  "sunset_view",
  "mountain_view",
  "quiet_night",
  "soundproofing",
  "room_orientation",
  "morning_sun",
  "evening_heat",
  "railway_noise",
]);
const normalizedName = (name: string) =>
  name
    .toLocaleLowerCase("ru")
    .replaceAll("ё", "е")
    .replace(/[^\p{L}\p{N}]+/gu, "")
    .trim();
function relatedHotels(results: SearchHit[], reference: Hotel) {
  const ids = new Set([reference.id]);
  const names = new Set([normalizedName(reference.name)]);
  return results
    .filter(({ hotel }) => {
      const name = normalizedName(hotel.name);
      if (ids.has(hotel.id) || names.has(name)) return false;
      ids.add(hotel.id);
      names.add(name);
      return true;
    })
    .slice(0, 3);
}
function RelatedHotels({ hotel }: { hotel: Hotel }) {
  // Independent profile browsing must never replace the user's edited query.
  const related = useResource(
    () => api.search({ similar_to: hotel.id, text: "", limit: 3 }),
    [hotel.id],
  );
  const candidates = related.data
    ? relatedHotels(related.data.results, hotel)
    : [];
  return (
    <section
      className="section-block related-hotels"
      data-testid="related-hotels"
    >
      <span className="eyebrow">ПРОФИЛИ ДЛЯ СРАВНЕНИЯ</span>
      <h2>Похожие отели</h2>
      <p className="muted section-lead">
        Сходство по известным характеристикам за сентябрь. Выбранные условия
        поездки сохраняются; совпадения профиля не подтверждают их совместимость
        в одном номере.
      </p>
      {related.loading && (
        <p role="status" className="muted">
          Сопоставляем известные профили…
        </p>
      )}
      <ErrorState error={related.error} retry={related.reload} />
      {!related.loading && !related.error && !candidates.length && (
        <p className="muted">
          Недостаточно сопоставимых данных для похожих профилей.
        </p>
      )}
      <div className="related-hotel-list">
        {candidates.map((hit) => {
          const similarities = (hit.similarity_reasons || []).filter(
            (reason) =>
              !/^Отличается|различается|близкое значение/i.test(reason) &&
              !/[:;]\s*(различается|близкое значение)/i.test(reason),
          );
          const differences = (hit.similarity_reasons || []).filter(
            (reason) => !similarities.includes(reason),
          );
          return (
            <article
              className="related-hotel-card"
              key={hit.hotel.id}
              data-testid={`related-hotel-${hit.hotel.id}`}
              data-hotel-id={hit.hotel.id}
            >
              <div className="related-hotel-title">
                <h3>
                  <Link to={`/hotel/${encodeURIComponent(hit.hotel.id)}`}>
                    {hit.hotel.name}
                  </Link>
                </h3>
                <CompareButton hotelId={hit.hotel.id} />
              </div>
              <p className="muted small">{hit.hotel.address}</p>
              <div className="related-profile-pair">
                <div>
                  <h4>Что схоже</h4>
                  {similarities.length ? (
                    <ul>
                      {similarities.map((text) => (
                        <li key={text}>{text.split(";")[0]}</li>
                      ))}
                    </ul>
                  ) : (
                    <p className="muted">Совпадения не выделены.</p>
                  )}
                </div>
                <div>
                  <h4>Чем отличается от этого отеля</h4>
                  {differences.length ? (
                    <ul>
                      {differences.map((text) => (
                        <li key={text}>{text.split(";")[0]}</li>
                      ))}
                    </ul>
                  ) : (
                    <p className="muted">
                      Сопоставимые различия не выделены в данных.
                    </p>
                  )}
                </div>
              </div>
              {!!hit.similarity_reasons?.length && (
                <details className="room-caveats-details">
                  <summary>Как посчитано</summary>
                  <ul>
                    {hit.similarity_reasons.map((reason) => (
                      <li key={reason}>{reason}</li>
                    ))}
                  </ul>
                </details>
              )}
              <Link
                className="text-button small"
                to={`/hotel/${encodeURIComponent(hit.hotel.id)}`}
              >
                Посмотреть факты <ArrowRight size={14} />
              </Link>
            </article>
          );
        })}
      </div>
      {candidates.length > 0 && (
        <Link className="text-button" to="/compare">
          Открыть сравнение выбранных отелей <ArrowRight size={15} />
        </Link>
      )}
    </section>
  );
}
export function HotelPage() {
  const { id = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const { specs, parsed } = useApp();
  const requestedRoomIndex = params.has("room_request")
    ? Number(params.get("room_request"))
    : 0;
  const roomIndex =
    Number.isInteger(requestedRoomIndex) &&
    requestedRoomIndex >= 0 &&
    requestedRoomIndex < (parsed?.rooms?.length || 0)
      ? requestedRoomIndex
      : 0;
  const roomRequest = parsed?.rooms?.[roomIndex];
  const previewQuery =
    parsed && roomRequest ? personalRoomQuery(parsed, roomRequest) : parsed;
  const detail = useResource(() => api.hotel(id, parsed), [id, parsed]),
    geoResource = useResource(() => api.geo(id), [id]);
  const [panorama, setPanorama] = useState<string | null>(null),
    [showGeo, setShowGeo] = useState(false),
    [evidence, setEvidence] = useState<Fact | null>(null);
  const [selection, setSelection] = useState<{
    hotelId: string;
    fact: Fact;
  } | null>(null);
  const [roomSlice, setRoomSlice] = useState<{
    hotelId: string;
    slice: RoomSlice;
  } | null>(null);
  const d = detail.data,
    geo = geoResource.data || d?.geo || {};
  const verdicts = d?.verdicts || [];
  const disappointments = d?.disappointments || [];
  const rec = d?.room_recommendation;
  const currentFact = (snapshot: Fact | null) =>
    snapshot
      ? d?.facts.find((fact) => fact.id === snapshot.id) ||
        d?.facts.find(
          (fact) =>
            fact.characteristic_id === snapshot.characteristic_id &&
            JSON.stringify(fact.scope) === JSON.stringify(snapshot.scope),
        ) ||
        null
      : null;
  const selectedFact = currentFact(
    selection?.hotelId === id ? selection.fact : null,
  );
  const currentEvidence = currentFact(evidence);
  const activeRoomSlice = roomSlice?.hotelId === id ? roomSlice.slice : null;
  const questionScope = (
    characteristicId: string,
    fact?: Fact,
    slice: RoomSlice | null = activeRoomSlice,
  ): Scope | undefined => {
    const propertyScope = specs.data?.find(
      (spec) => spec.id === characteristicId,
    )?.scope;
    if (
      propertyScope === "hotel" ||
      (!propertyScope && !roomQuestionTraits.has(characteristicId))
    )
      return undefined;
    if (slice)
      return {
        type: "floor_range",
        facade: slice.facade,
        floor_min: slice.floor,
        floor_max: slice.floor,
        month: slice.month,
      };
    return selectedFact?.characteristic_id === characteristicId
      ? selectedFact.scope
      : fact?.scope;
  };
  const panoramaCharacteristic =
    selectedFact?.characteristic_id || "room_orientation";
  const panoramaScope = questionScope(panoramaCharacteristic);
  const hasRoomRays =
    activeRoomSlice &&
    geo.view_cones?.features.some(
      (feature) =>
        feature.properties?.facade === activeRoomSlice.facade &&
        (!feature.properties?.floor ||
          Number(feature.properties.floor) === activeRoomSlice.floor) &&
        (!feature.properties?.min_floor ||
          Number(feature.properties.min_floor) <= activeRoomSlice.floor),
    );
  const selectedSide =
    activeRoomSlice?.facade ||
    selectedFact?.scope.facade ||
    (selectedFact?.value &&
    typeof selectedFact.value === "object" &&
    "facade" in selectedFact.value
      ? String(selectedFact.value.facade || "")
      : undefined);
  const focusFact = (fact: Fact, target: "card" | "map" = "card") => {
    setRoomSlice(null);
    setSelection({ hotelId: id, fact });
    const node = document.getElementById(
      target === "card"
        ? factCardId(fact.characteristic_id)
        : `hotel-map-${id}`,
    );
    node?.scrollIntoView({ behavior: "smooth", block: "center" });
    node?.focus({ preventScroll: true });
  };
  const focusVerdict = (verdict: Verdict) => {
    const own = d?.facts.filter((fact) => verdict.fact_ids.includes(fact.id));
    const fact =
      own?.find(
        (f) => !f.scope.month || f.scope.month === (parsed?.month || 9),
      ) ||
      own?.[0] ||
      d?.facts.find((f) => f.characteristic_id === verdict.characteristic_id);
    if (fact) focusFact(fact);
  };
  // Turn the panorama towards the side that matters most for this hotel.
  const bestSide =
    ["sunset_view", "sea_view", "mountain_view"]
      .map(
        (id) =>
          d?.facts.find(
            (f) =>
              f.characteristic_id === id &&
              f.scope.facade &&
              (f.value as { available?: boolean } | null)?.available === true &&
              (id !== "sunset_view" || f.scope.month === (parsed?.month || 9)),
          )?.scope.facade,
      )
      .find(Boolean) || "S";
  const valueConflicts = (d?.conflicts || []).filter((c) => c.kind === "value");
  const scopeConflicts = (d?.conflicts || []).filter((c) => c.kind !== "value");
  const normals = new Map<Side, Set<number>>();
  for (const facade of geoResource.data?.facades || []) {
    if (
      !(facade.code in SIDES) ||
      typeof facade.azimuth_deg !== "number" ||
      !Number.isFinite(facade.azimuth_deg)
    )
      continue;
    const side = facade.code as Side;
    const values = normals.get(side) || new Set<number>();
    values.add(((facade.azimuth_deg % 360) + 360) % 360);
    normals.set(side, values);
  }
  const facadeAzimuths: FacadeAzimuths = {};
  for (const [side, values] of normals) {
    if (values.size === 1) facadeAzimuths[side] = [...values][0];
  }
  const catalogueReview = record(d?.hotel.tags?.catalogue_review);
  if (catalogueReview.disposition === "hold_for_identity_review")
    return (
      <div className="page-container">
        <BackLink />
        <h1>{d?.hotel.name}</h1>
        <div className="notice" role="status">
          <strong>Объект временно скрыт из подбора</strong>
          <p>{String(catalogueReview.reason_ru)}</p>
          <p>
            Идентичность или координаты требуют проверки. Расчёты исходной точки
            не используются для выбора номера.
          </p>
          <a href="/data/seed/catalogue_reviews.json">
            Источники и основания проверки
          </a>
        </div>
        <Link className="button primary" to="/search">
          Выбрать другой отель
        </Link>
      </div>
    );
  return (
    <div className="hotel-page page-container">
      <BackLink />
      {detail.loading && <Loading />}
      <ErrorState error={detail.error} retry={detail.reload} />
      {d && (
        <>
          <div className="hotel-title-row">
            <div>
              <span className="eyebrow">СОЧИ · ОТЕЛЬ В ДЕТАЛЯХ</span>
              <h1>{d.hotel.name}</h1>
              <p>
                <MapPin size={15} />
                {d.hotel.address}
                {d.hotel.stars && (
                  <span className="inline-stars">
                    {"★".repeat(d.hotel.stars)}
                  </span>
                )}
              </p>
            </div>
            <CompareButton hotelId={id} />
          </div>
          <div className="hotel-hero-grid">
            <div className="detail-visual-wrap">
              <HotelVisual hotel={d.hotel} className="detail-visual" credit />
              <button
                className="button panorama-button"
                onClick={() => setPanorama(selectedSide || bestSide)}
                data-testid="open-panorama"
              >
                <Eye size={16} />
                Панорама рядом
              </button>
            </div>
            <div
              className="hotel-hero-map"
              id={`hotel-map-${id}`}
              tabIndex={-1}
              data-selected-trait={
                activeRoomSlice
                  ? "room_orientation"
                  : selectedFact?.characteristic_id
              }
            >
              <TravelMap
                hotels={[d.hotel]}
                activeId={id}
                geo={geo}
                month={activeRoomSlice?.month || parsed?.month || 9}
                facade={selectedSide}
                floor={
                  activeRoomSlice?.floor ||
                  selectedFact?.scope.floor_min ||
                  undefined
                }
                focusTrait={
                  activeRoomSlice
                    ? "room_orientation"
                    : selectedFact?.characteristic_id
                }
              />
              <button
                className="button map-calc-button"
                onClick={() => setShowGeo(true)}
                data-testid="show-calculation"
              >
                <Compass size={17} />
                Показать расчёт <ArrowUpRight size={16} />
              </button>
              {activeRoomSlice && (
                <div
                  className="hotel-map-context"
                  data-testid="room-lab-map-context"
                >
                  <p>
                    <strong>Выбранные условия</strong> ·{" "}
                    {SIDES[activeRoomSlice.facade]} · {activeRoomSlice.floor}-й{" "}
                    {d.hotel.floors ? "этаж" : "условный уровень"}
                  </p>
                  <span className="muted small">
                    Сторону и этаж проверили по данным. Номер и расположение
                    окон подтвердит отель.
                  </span>
                  {!hasRoomRays && (
                    <p className="muted small" data-testid="room-lab-rays-note">
                      Для этого этажа нет рисунка обзора. На карте выделена
                      сторона здания; результат расчёта — в проверке окна.
                    </p>
                  )}
                  <div className="hotel-map-context-actions">
                    <AskButton
                      hotelId={id}
                      characteristicId="room_orientation"
                      scope={questionScope("room_orientation")}
                      compact
                    />
                    <button
                      className="text-button small"
                      onClick={() =>
                        document.getElementById("room-lab")?.scrollIntoView({
                          behavior: "smooth",
                          block: "center",
                        })
                      }
                    >
                      К проверке окна
                    </button>
                    <button
                      className="text-button small"
                      onClick={() => setPanorama(activeRoomSlice.facade)}
                    >
                      <Eye size={14} /> Панорама стороны
                    </button>
                  </div>
                </div>
              )}
              {selectedFact && (
                <div
                  className="hotel-map-context"
                  data-testid="hotel-map-context"
                  data-fact-id={selectedFact.id}
                >
                  <p>
                    <strong>
                      {specs.data?.find(
                        (s) => s.id === selectedFact.characteristic_id,
                      )?.name_ru ||
                        TRAIT_NAMES[selectedFact.characteristic_id] ||
                        selectedFact.characteristic_id}
                    </strong>
                    {selectedSide &&
                      ` · ${SIDES[selectedSide] || selectedSide}`}
                    {selectedFact.scope.floor_min &&
                      ` · с ${selectedFact.scope.floor_min}-го этажа`}
                  </p>
                  <span className="muted small">
                    Показаны доступные данные. Наличие окна и условия номера
                    уточните у отеля.
                  </span>
                  <div className="hotel-map-context-actions">
                    <button
                      className="text-button small"
                      onClick={() => focusFact(selectedFact)}
                    >
                      К факту
                    </button>
                    <button
                      className="text-button small"
                      onClick={() => setEvidence(selectedFact)}
                    >
                      Основание
                    </button>
                    {selectedSide && (
                      <button
                        className="text-button small"
                        data-testid="selected-panorama"
                        onClick={() => setPanorama(selectedSide)}
                      >
                        <Eye size={14} /> Панорама: {SIDES[selectedSide]}
                      </button>
                    )}
                    <AskButton
                      hotelId={id}
                      characteristicId={selectedFact.characteristic_id}
                      scope={questionScope(selectedFact.characteristic_id)}
                      compact
                    />
                    <button
                      className="text-button small"
                      onClick={() => setSelection(null)}
                    >
                      Все фасады
                    </button>
                  </div>
                </div>
              )}
            </div>
          </div>
          <div className="hotel-content-grid">
            <div className="hotel-main">
              <section className="hotel-fit section-block">
                <div className="section-heading">
                  <div>
                    <span className="eyebrow">НЕ ПРОСТО ХОРОШИЙ. ВАШ.</span>
                    <h2>
                      {parsed?.rooms?.length ? "Для всех" : "Под ваши ожидания"}
                    </h2>
                  </div>
                  <ShieldCheck size={26} strokeWidth={1.2} />
                </div>
                {parsed && <p className="muted query-quote">«{parsed.text}»</p>}
                {verdicts.length ? (
                  <VerdictList
                    verdicts={verdicts}
                    hotelId={id}
                    questionScope={(verdict) =>
                      questionScope(
                        verdict.characteristic_id,
                        d.facts.find((fact) =>
                          verdict.fact_ids.includes(fact.id),
                        ),
                      )
                    }
                    {...{ onFocusFact: focusVerdict }}
                    onEvidence={(v) => {
                      const fact =
                        d.facts.find((f) => v.fact_ids.includes(f.id)) ||
                        d.facts.find(
                          (f) => f.characteristic_id === v.characteristic_id,
                        );
                      if (fact) setEvidence(fact);
                    }}
                  />
                ) : (
                  <p className="muted">
                    Выберите условия в поиске, чтобы сопоставить их с этим
                    отелем. Доступные факты — ниже.
                  </p>
                )}
              </section>
              {!!d.room_plan?.length && (
                <GroupPlan
                  hotelId={id}
                  unknownFloors={d.hotel.floors === null}
                  items={d.room_plan}
                  requests={parsed?.rooms}
                  onOpen={(index, item) => {
                    const next = roomLabParams(item.scope, index);
                    setParams(next, { replace: true });
                    window.setTimeout(
                      () =>
                        document.getElementById("room-lab")?.scrollIntoView({
                          behavior: window.matchMedia(
                            "(prefers-reduced-motion: reduce)",
                          ).matches
                            ? "instant"
                            : "smooth",
                          block: "start",
                        }),
                      100,
                    );
                  }}
                />
              )}
              <RoomAdviceSection
                hotelId={id}
                month={parsed?.month || 9}
                recommendation={
                  !d.room_plan?.length && parsed ? rec : undefined
                }
                profile={
                  !d.room_plan?.length && parsed ? d.room_profile : undefined
                }
                onSelect={(scope) => {
                  setParams(roomLabParams(scope), { replace: true });
                  window.setTimeout(
                    () =>
                      document.getElementById("room-lab")?.scrollIntoView({
                        behavior: "smooth",
                        block: "start",
                      }),
                    100,
                  );
                }}
              >
                {!d.room_plan?.length && parsed && d.room_profile && (
                  <RoomDescription
                    hotelId={id}
                    profile={d.room_profile}
                    unknownFloors={d.hotel.floors === null}
                  />
                )}
              </RoomAdviceSection>
              {!geoResource.loading && (
                <RoomLab
                  key={`${id}:${params.toString()}`}
                  detail={d}
                  geo={geo}
                  parsed={previewQuery}
                  roomLabel={roomRequest?.label}
                  requestedScope={roomRequest?.scope}
                  roomIndex={roomRequest ? roomIndex : undefined}
                  mapActive={!!activeRoomSlice}
                  onSliceChange={(slice) => {
                    setRoomSlice(slice ? { hotelId: id, slice } : null);
                    if (slice) setSelection(null);
                  }}
                  onEvidence={setEvidence}
                  onPanorama={setPanorama}
                  questionScope={(characteristicId, slice) =>
                    questionScope(characteristicId, undefined, slice)
                  }
                />
              )}
              <section className="disappointments-section section-block">
                <span className="eyebrow">
                  <TriangleAlert size={14} /> ЛУЧШЕ ЗНАТЬ СЕЙЧАС
                </span>
                <h2>Что может разочаровать</h2>
                {disappointments.length ? (
                  <ul>
                    {disappointments.map((t, i) => (
                      <li key={t}>
                        <span>0{i + 1}</span>
                        <p>{t}</p>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="muted">
                    Отдельные ограничения пока не найдены. Это не гарантия их
                    отсутствия — уточните критичные детали у отеля.
                  </p>
                )}
              </section>
              <section className="all-facts section-block">
                <span className="eyebrow">ПОЛНАЯ КАРТИНА</span>
                <h2>Факты и их основания</h2>
                <p className="muted section-lead">
                  Каждое значение рассчитано по открытым данным или получено от
                  отеля. Нажмите на значение, чтобы увидеть источник, метод и
                  дату.
                </p>
                {!d.facts.length && (
                  <Empty title="Профиль ещё собирается">
                    Пока нет достаточных данных для утверждений об отеле.
                  </Empty>
                )}
                {d.facts.length > 0 && (
                  <FactsPanel
                    facts={d.facts}
                    claims={d.claims}
                    specs={specs.data || []}
                    hotelId={id}
                    month={parsed?.month || 9}
                    facadeAzimuths={facadeAzimuths}
                    onEvidence={setEvidence}
                    questionScope={questionScope}
                    onPanorama={setPanorama}
                    onShowOnMap={(fact) => focusFact(fact, "map")}
                    selectedTrait={selectedFact?.characteristic_id}
                  />
                )}
              </section>
              {valueConflicts.length > 0 && (
                <section
                  className="section-block conflicts-section"
                  data-testid="value-conflicts"
                >
                  <span className="eyebrow warning-eyebrow">
                    <TriangleAlert size={14} /> ИСТОЧНИКИ РАСХОДЯТСЯ
                  </span>
                  <h2>Где данные спорят</h2>
                  {valueConflicts.map((c) => (
                    <div className="conflict-card value" key={c.id}>
                      <div className="conflict-head">
                        <strong>
                          {specs.data?.find((s) => s.id === c.characteristic_id)
                            ?.name_ru || c.characteristic_id}
                        </strong>
                        <span className="badge warning">
                          {conflictStatus[c.status] || c.status}
                        </span>
                      </div>
                      <p>{c.resolution_note}</p>
                      {c.status === "asked" ? (
                        <span className="muted small">
                          Вопрос уже в кабинете отеля. До ответа значение не
                          считается подтверждённым.
                        </span>
                      ) : (
                        <AskButton
                          hotelId={id}
                          characteristicId={c.characteristic_id}
                        />
                      )}
                    </div>
                  ))}
                </section>
              )}
              {scopeConflicts.length > 0 && (
                <details className="section-block scope-conflicts">
                  <summary>
                    Уточнения по сторонам здания ({scopeConflicts.length})
                  </summary>
                  <p className="muted">
                    Общая фраза источника («вид на море») уточнена расчётом по
                    фасадам и этажам. Это не противоречие, а детализация.
                  </p>
                  {scopeConflicts.slice(0, 6).map((c) => (
                    <p className="fine-print" key={c.id}>
                      {c.resolution_note}
                    </p>
                  ))}
                </details>
              )}
              <RelatedHotels hotel={d.hotel} />
            </div>
            <aside className="booking-aside">
              <span className="eyebrow">ОТДЫХ БЕЗ НЕДОСКАЗАННОСТИ</span>
              <h3>
                Договоритесь
                <br />о важном заранее.
              </h3>
              <p>
                Соберём ожидания в один паспорт. Отель подтвердит их для вашего
                номера.
              </p>
              <div className="booking-price">
                {d.hotel.price_rub !== null ? (
                  <>
                    <strong>{d.hotel.price_rub.toLocaleString("ru")} ₽</strong>
                    <span> / ночь</span>
                    {d.hotel.basic_fields_source === "mock_basic_card" && (
                      <Synthetic>Демонстрационная цена</Synthetic>
                    )}
                  </>
                ) : (
                  <span>Стоимость уточнит отель</span>
                )}
              </div>
              <Link
                className="button primary"
                to={`/booking/${id}`}
                data-testid="book-with-passport"
              >
                Создать паспорт <ArrowRight size={17} />
              </Link>
              <p className="fine-print">
                Демонстрационная бронь без оплаты. Паспорт фиксирует ожидания, а
                не гарантирует наличие номера.
              </p>
              {d.hotel.website && (
                <a
                  href={d.hotel.website}
                  target="_blank"
                  rel="noreferrer"
                  className="text-button"
                >
                  Сайт отеля <ArrowUpRight size={14} />
                </a>
              )}
            </aside>
          </div>
          {showGeo && (
            <GeoModal
              hotel={d.hotel}
              geo={geo}
              onClose={() => setShowGeo(false)}
            />
          )}
          {panorama && (
            <PanoramaModal
              hotel={d.hotel}
              side={panorama}
              onClose={() => setPanorama(null)}
              footer={
                <div
                  className="panorama-question"
                  data-testid="panorama-question"
                >
                  <p className="muted small">
                    Уличная панорама не подтверждает условия номера. Уточните их
                    у отеля.
                  </p>
                  {panoramaScope && (
                    <p
                      className="muted small"
                      data-testid="panorama-question-scope"
                    >
                      Пожелание к окну: {scopeText(panoramaScope)}. Это не
                      подтверждение номера или наличия мест.
                    </p>
                  )}
                  <AskButton
                    hotelId={id}
                    characteristicId={panoramaCharacteristic}
                    scope={panoramaScope}
                    compact
                  />
                </div>
              }
            />
          )}
          {currentEvidence && (
            <EvidenceModal
              key={JSON.stringify(currentEvidence)}
              fact={currentEvidence}
              claims={d.claims}
              onClose={() => setEvidence(null)}
            />
          )}
        </>
      )}
    </div>
  );
}
