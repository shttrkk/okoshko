import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  ArrowRight,
  ArrowUpRight,
  Compass,
  MapPin,
  ShieldCheck,
  Sun,
  TriangleAlert,
  Waves,
} from "lucide-react";
import { api, useResource } from "../api";
import { useApp } from "../state";
import { GeoModal, TravelMap } from "../components/Map";
import { FactsPanel } from "../components/FactsPanel";
import {
  AskButton,
  BackLink,
  CompareButton,
  Empty,
  ErrorState,
  EvidenceModal,
  HotelVisual,
  Loading,
  Synthetic,
  VerdictList,
} from "../components/ui";
import type { Fact } from "../types";
export function HotelPage() {
  const { id = "" } = useParams();
  const { specs, parsed } = useApp();
  const detail = useResource(() => api.hotel(id, parsed), [id, parsed]),
    geoResource = useResource(() => api.geo(id), [id]);
  const [showGeo, setShowGeo] = useState(false),
    [evidence, setEvidence] = useState<Fact | null>(null);
  const d = detail.data,
    geo = geoResource.data || d?.geo || {};
  const verdicts = d?.verdicts || [];
  const disappointments = d?.disappointments || [];
  const rec = d?.room_recommendation;
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
            <HotelVisual hotel={d.hotel} className="detail-visual" credit />
            <div className="hotel-hero-map">
              <TravelMap
                hotels={[d.hotel]}
                activeId={id}
                geo={geo}
                month={parsed?.month || 9}
              />
              <button
                className="button map-calc-button"
                onClick={() => setShowGeo(true)}
                data-testid="show-calculation"
              >
                <Compass size={17} />
                Показать расчёт <ArrowUpRight size={16} />
              </button>
            </div>
          </div>
          <div className="hotel-content-grid">
            <div className="hotel-main">
              <section className="hotel-fit section-block">
                <div className="section-heading">
                  <div>
                    <span className="eyebrow">НЕ ПРОСТО ХОРОШИЙ. ВАШ.</span>
                    <h2>Под ваши ожидания</h2>
                  </div>
                  <ShieldCheck size={26} strokeWidth={1.2} />
                </div>
                {parsed && <p className="muted query-quote">«{parsed.text}»</p>}
                {verdicts.length ? (
                  <VerdictList
                    verdicts={verdicts}
                    hotelId={id}
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
              <section className="room-section section-block">
                <div className="room-compass" aria-hidden="true">
                  <span className="north">С</span>
                  <Sun className="room-sun" />
                  <div className="room-building">
                    <i />
                    <i />
                    <i />
                    <i />
                    <i />
                    <i />
                  </div>
                  <Waves className="room-waves" />
                </div>
                <div>
                  <span className="eyebrow">ВПЕЧАТЛЕНИЯ ЖИВУТ В НОМЕРЕ</span>
                  <h2>Какой номер просить</h2>
                  <p
                    className="room-recommendation"
                    data-testid="room-recommendation"
                  >
                    {rec}
                  </p>
                  <p className="fine-print">
                    Совпадение условий нужно подтвердить для одного номера:
                    стороны, этажа и типа. Схема иллюстративная, точная
                    геометрия — в расчёте.
                  </p>
                  <Link className="text-button" to={`/booking/${id}`}>
                    Добавить в паспорт ожиданий <ArrowRight size={16} />
                  </Link>
                </div>
              </section>
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
                    API не выделил отдельных ограничений. Это не гарантия их
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
                    onEvidence={setEvidence}
                  />
                )}
              </section>
              {d.conflicts.length > 0 && (
                <section className="section-block">
                  <h2>Открытые вопросы</h2>
                  {d.conflicts.map((c) => (
                    <div className="conflict-card" key={c.id}>
                      <span className="badge warning">{c.status}</span>
                      <p>{c.resolution_note}</p>
                      <AskButton
                        hotelId={id}
                        characteristicId={c.characteristic_id}
                      />
                    </div>
                  ))}
                </section>
              )}
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
          {evidence && (
            <EvidenceModal
              fact={evidence}
              claims={d.claims}
              onClose={() => setEvidence(null)}
            />
          )}
        </>
      )}
    </div>
  );
}
