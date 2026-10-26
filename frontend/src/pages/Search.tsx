import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import {
  ArrowDown,
  ArrowRight,
  ArrowUpRight,
  Footprints,
  Heart,
  MapPin,
  Moon,
  PawPrint,
  Search,
  SlidersHorizontal,
  Sparkles,
  Sun,
  Waves,
  X,
} from "lucide-react";
import { api, useResource } from "../api";
import { useApp } from "../state";
import { TravelMap } from "../components/Map";
import {
  CompareButton,
  Empty,
  ErrorState,
  EvidenceModal,
  HotelVisual,
  Loading,
  Modal,
  Synthetic,
  VerdictList,
} from "../components/ui";
import type {
  Criterion,
  Fact,
  HotelDetail,
  ParsedQuery,
  SearchHit,
  SearchRequest,
  Verdict,
} from "../types";
export const mashaQuery = "тихий номер с видом на закат недалеко от моря";
const personas = [
  {
    icon: Moon,
    title: "Выспаться",
    text: "Тихий номер, окна во двор и плотные шторы",
    query: "тихий номер с хорошей шумоизоляцией и плотными шторами",
  },
  {
    icon: Sun,
    title: "Закат для двоих",
    text: "Море в окне. И никаких случайностей.",
    query: mashaQuery,
  },
  {
    icon: Footprints,
    title: "С детьми и коляской",
    text: "До пляжа без лестниц и сложных переходов",
    query:
      "до пляжа с коляской без лестниц и перехода трассы, рядом детская площадка",
  },
  {
    icon: Waves,
    title: "Работать у моря",
    text: "Связь для созвонов, тишина для мыслей",
    query: "стабильный интернет не меньше 30 Мбит, тихо днём, рядом море",
  },
  {
    icon: PawPrint,
    title: "На машине с собакой",
    text: "Парковка рядом и место для прогулок",
    query: "с собакой 30 кг, парковка у отеля, рядом где выгулять",
  },
  {
    icon: Heart,
    title: "В гуще событий",
    text: "Кафе, вечерние прогулки и живая набережная",
    query: "кафе и бары рядом, в центре событий, недалеко от набережной",
  },
];
export function SearchForm({
  initial = mashaQuery,
  compact = false,
  similar = false,
}: {
  initial?: string;
  compact?: boolean;
  similar?: boolean;
}) {
  const navigate = useNavigate(),
    { hotels } = useApp();
  const [text, setText] = useState(initial),
    [checkIn, setCheckIn] = useState("2027-09-14"),
    [checkOut, setCheckOut] = useState("2027-09-21"),
    [similarId, setSimilarId] = useState(""),
    [isSimilar, setIsSimilar] = useState(similar);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const params = new URLSearchParams({
      q: text,
      check_in: checkIn,
      check_out: checkOut,
    });
    if (isSimilar && similarId) params.set("similar_to", similarId);
    navigate(`/search?${params}`);
  };
  return (
    <form
      className={`search-form ${compact ? "compact" : ""}`}
      onSubmit={submit}
      data-testid="search-form"
    >
      <label className="search-input-label">
        <span className="sr-only">Расскажите, какой отдых вы хотите</span>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          maxLength={2000}
          placeholder="Тихий номер с видом на закат недалеко от моря…"
          rows={compact ? 2 : 3}
          data-testid="search-input"
          required={!isSimilar}
        />
      </label>
      <div className="search-form-bottom">
        <div className="date-inputs">
          <label>
            <span>Заезд</span>
            <input
              aria-label="Дата заезда"
              type="date"
              value={checkIn}
              onChange={(e) => setCheckIn(e.target.value)}
              required
              data-testid="check-in"
            />
          </label>
          <span className="date-dash">—</span>
          <label>
            <span>Выезд</span>
            <input
              aria-label="Дата выезда"
              type="date"
              value={checkOut}
              min={checkIn}
              onChange={(e) => setCheckOut(e.target.value)}
              required
              data-testid="check-out"
            />
          </label>
        </div>
        <button
          className="button primary"
          type="submit"
          data-testid="search-submit"
        >
          Найти мой отель <ArrowRight size={19} />
        </button>
      </div>
      {!compact && (
        <div className="similar-controls">
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={isSimilar}
              onChange={(e) => setIsSimilar(e.target.checked)}
            />
            Найти похожий на любимый отель
          </label>
          {isSimilar && (
            <select
              aria-label="Любимый отель"
              value={similarId}
              onChange={(e) => setSimilarId(e.target.value)}
              required
            >
              <option value="">Выберите отель</option>
              {hotels.data?.map((h) => (
                <option key={h.id} value={h.id}>
                  {h.name}
                </option>
              ))}
            </select>
          )}
        </div>
      )}
    </form>
  );
}
export function Home() {
  const { hotels, status } = useApp();
  const navigate = useNavigate();
  return (
    <div className="home">
      <section className="hero">
        <div className="hero-copy">
          <span className="eyebrow">
            <i className="tiny-dot" /> ОТДЫХ, В КОТОРОМ ВСЁ СОШЛОСЬ
          </span>
          <h1>
            Ваше море.
            <br />
            Ваш закат.
            <br />
            <em>Ваше окно.</em>
          </h1>
          <p className="hero-description">
            Отель может быть прекрасным.
            <br />А номер — совсем не вашим.
            <br />
            Найдём тот, где совпадёт важное.
          </p>
          <div className="hero-meta">
            <MapPin size={15} />
            <span>Демо-регион: Сочи</span>
            <span className="meta-line" />
            <span>
              центр + Адлер–Имеретинка ·{" "}
              {status.data?.hotel_count ?? hotels.data?.length ?? "…"} отелей
            </span>
          </div>
        </div>
        <div className="hero-map-wrap">
          <TravelMap hero hotels={(hotels.data || []).slice(0, 12)} />
          <div className="hero-map-note">
            <span className="note-sun">
              <Sun size={23} />
            </span>
            <div>
              <strong>У каждого окна — своя история</strong>
              <span>Рельеф, фасады, солнце и дорога к морю</span>
            </div>
          </div>
          <div className="map-stamp">
            СОЧИ
            <br />
            <span>МЕЖДУ ГОРАМИ И МОРЕМ</span>
          </div>
        </div>
      </section>
      <section className="home-search">
        <div className="search-section-label">
          <span className="eyebrow">01 / РАССКАЖИТЕ О ГЛАВНОМ</span>
          <span>Остальное проверим по данным</span>
        </div>
        <h2>Каким будет ваш отдых?</h2>
        <SearchForm />
        <div className="search-trust">
          <span>
            <Sparkles size={15} />
            Одна фраза вместо десятка фильтров
          </span>
          <span>? — данных пока нет. Это не значит «нет».</span>
        </div>
      </section>
      <section className="scenario-section">
        <div className="section-heading">
          <div>
            <span className="eyebrow">У КАЖДОГО СВОЁ «ХОРОШО»</span>
            <h2>С чего начнём?</h2>
          </div>
          <ArrowDown size={23} />
        </div>
        <div className="scenario-grid">
          {personas.map((p, i) => (
            <button
              key={p.title}
              className={`scenario-card scenario-${i}`}
              onClick={() =>
                navigate(
                  `/search?q=${encodeURIComponent(p.query)}&check_in=2027-09-14&check_out=2027-09-21`,
                )
              }
            >
              <p.icon size={25} strokeWidth={1.3} />
              <span className="scenario-number">0{i + 1}</span>
              <h3>{p.title}</h3>
              <p>{p.text}</p>
              <ArrowUpRight className="scenario-arrow" size={20} />
            </button>
          ))}
        </div>
      </section>
      <section className="promise-section">
        <span className="eyebrow">ЗНАТЬ ДО ЗАЕЗДА</span>
        <h2>
          «Вид на море» —<br />
          это ещё не вся картина.
        </h2>
        <div>
          <p>
            Какой фасад? С какого этажа? Видно ли закат в сентябре? Сколько идти
            обратно с пляжа?
          </p>
          <p className="muted">
            Каждое утверждение можно раскрыть до источника, метода и даты. Что
            нельзя рассчитать — спросим у отеля.
          </p>
          <Link to="/sources" className="text-button">
            Как устроено О!кно <ArrowRight size={17} />
          </Link>
        </div>
      </section>
      <section className="home-data-note">
        <span className="connection-dot connected" />
        {status.data
          ? `${status.data.hotel_count ?? hotels.data?.length ?? "…"} отелей · ${status.data.characteristic_count ?? "…"} характеристик · ${status.data.mode}`
          : "Подключаем каталог реальных отелей"}
        <Link to="/quality">
          Как проверяем качество <ArrowUpRight size={13} />
        </Link>
      </section>
    </div>
  );
}
function CriteriaEditor({
  parsed,
  onChange,
  onApply,
  busy,
}: {
  parsed: ParsedQuery;
  onChange: (p: ParsedQuery) => void;
  onApply: () => void;
  busy: boolean;
}) {
  const { specs } = useApp();
  const [drag, setDrag] = useState<{
    id: string;
    group: "must" | "nice";
  } | null>(null);
  const [showDates, setShowDates] = useState(false);
  const monthName = new Intl.DateTimeFormat("ru", { month: "long" }).format(
    new Date(2026, parsed.month - 1, 1),
  );
  const noDates = !parsed.check_in && !parsed.check_out;
  const dateError = !!(
    parsed.check_in &&
    parsed.check_out &&
    parsed.check_out <= parsed.check_in
  );
  const move = (group: "must" | "nice", id: string) => {
    const target = group === "must" ? "nice" : "must",
      criterion = parsed[group].find((c) => c.characteristic_id === id);
    if (criterion)
      onChange({
        ...parsed,
        [group]: parsed[group].filter((c) => c !== criterion),
        [target]: [...parsed[target], criterion],
      });
  };
  const edit = (
    group: "must" | "nice",
    id: string,
    change: Partial<Criterion>,
  ) =>
    onChange({
      ...parsed,
      [group]: parsed[group].map((c) =>
        c.characteristic_id === id ? { ...c, ...change } : c,
      ),
    });
  const drop = (group: "must" | "nice") => {
    if (drag && drag.group !== group) move(drag.group, drag.id);
    setDrag(null);
  };
  return (
    <section className="criteria-panel" aria-label="Условия поиска">
      <div className="criteria-top">
        <span>
          <SlidersHorizontal size={16} />
          Ваши ориентиры
        </span>
        <span className="fine-print">Можно менять</span>
      </div>
      <div className="date-assumption">
        <button
          className="date-assumption-trigger"
          onClick={() => setShowDates(!showDates)}
          aria-expanded={showDates}
          data-testid={noDates ? "default-month-chip" : "trip-dates-chip"}
        >
          {noDates ? (
            <>
              <strong>
                {monthName.charAt(0).toUpperCase() + monthName.slice(1)}
                {parsed.month === 9 ? " (по умолчанию)" : ""}
              </strong>
              <span>— укажите даты</span>
            </>
          ) : (
            <>
              <strong>
                {parsed.check_in || "Заезд не указан"} —{" "}
                {parsed.check_out || "Выезд не указан"}
              </strong>
              <span>Изменить даты</span>
            </>
          )}
          <SlidersHorizontal size={12} />
        </button>
        {showDates && (
          <div className="criteria-dates">
            <label>
              Месяц поездки
              <select
                value={parsed.month}
                disabled={!!parsed.check_in}
                onChange={(e) =>
                  onChange({ ...parsed, month: Number(e.target.value) })
                }
                aria-label="Месяц поездки"
              >
                {Array.from({ length: 12 }, (_, i) => (
                  <option value={i + 1} key={i}>
                    {new Intl.DateTimeFormat("ru", { month: "long" }).format(
                      new Date(2026, i, 1),
                    )}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Заезд
              <input
                aria-label="Уточнить дату заезда"
                type="date"
                value={parsed.check_in || ""}
                onChange={(e) =>
                  onChange({
                    ...parsed,
                    check_in: e.target.value || null,
                    month: e.target.value
                      ? new Date(e.target.value).getUTCMonth() + 1
                      : parsed.month,
                  })
                }
              />
            </label>
            <label>
              Выезд
              <input
                aria-label="Уточнить дату выезда"
                type="date"
                value={parsed.check_out || ""}
                min={parsed.check_in || undefined}
                onChange={(e) =>
                  onChange({ ...parsed, check_out: e.target.value || null })
                }
              />
            </label>
            <button
              className="text-button"
              onClick={() =>
                onChange({
                  ...parsed,
                  check_in: null,
                  check_out: null,
                  month: 9,
                })
              }
            >
              Сбросить даты
            </button>
            <p className="fine-print">
              От месяца зависит расчёт заката и климат. Нажмите «Применить»,
              чтобы обновить выдачу.
            </p>
            {dateError && (
              <p role="alert" className="inline-error">
                Выезд должен быть позже заезда.
              </p>
            )}
          </div>
        )}
      </div>
      {(["must", "nice"] as const).map((group) => (
        <div
          className="criteria-group"
          key={group}
          onDragOver={(e) => e.preventDefault()}
          onDrop={() => drop(group)}
        >
          <div className="criteria-label">
            {group === "must" ? "Обязательно" : "Желательно"}
            <span>{parsed[group].length}</span>
          </div>
          <div className="chips">
            {parsed[group].map((c) => (
              <div
                className={`criterion-chip ${group}`}
                draggable
                onDragStart={() => setDrag({ id: c.characteristic_id, group })}
                key={c.characteristic_id}
              >
                <span>{c.label}</span>
                {typeof c.value === "number" && (
                  <label className="chip-threshold">
                    <span>{c.op === "lte" ? "≤" : "≥"}</span>
                    <input
                      type="number"
                      aria-label={`Порог: ${c.label}`}
                      value={c.value}
                      min="0"
                      max="9999"
                      onChange={(e) =>
                        edit(group, c.characteristic_id, {
                          value: Number(e.target.value),
                        })
                      }
                    />
                  </label>
                )}
                <button
                  title={
                    group === "must"
                      ? "Сделать желательным"
                      : "Сделать обязательным"
                  }
                  aria-label={`Переместить ${c.label} в ${group === "must" ? "желательные" : "обязательные"}`}
                  onClick={() => move(group, c.characteristic_id)}
                >
                  {group === "must" ? "↓" : "↑"}
                </button>
                <button
                  aria-label={`Убрать ${c.label}`}
                  onClick={() =>
                    onChange({
                      ...parsed,
                      [group]: parsed[group].filter((i) => i !== c),
                    })
                  }
                >
                  <X size={12} />
                </button>
              </div>
            ))}
            {!parsed[group].length && (
              <span className="muted small">
                Перетащите условие или добавьте новое
              </span>
            )}
          </div>
        </div>
      ))}
      <div className="criteria-bottom">
        <select
          aria-label="Добавить условие"
          value=""
          onChange={(e) => {
            const s = specs.data?.find((s) => s.id === e.target.value);
            if (s)
              onChange({
                ...parsed,
                nice: [
                  ...parsed.nice,
                  {
                    characteristic_id: s.id,
                    label: s.name_ru,
                    op: s.search.default_op,
                    value: s.search.default_value,
                    field: s.search.field,
                    weight: 1,
                  },
                ],
              });
          }}
        >
          <option value="">+ Добавить условие</option>
          {specs.data
            ?.filter(
              (s) =>
                ![...parsed.must, ...parsed.nice].some(
                  (c) => c.characteristic_id === s.id,
                ),
            )
            .map((s) => (
              <option key={s.id} value={s.id}>
                {s.name_ru}
              </option>
            ))}
        </select>
        <button
          className="text-button"
          onClick={onApply}
          disabled={busy || dateError}
        >
          Применить <ArrowRight size={15} />
        </button>
      </div>
      {parsed.unmapped.length > 0 && (
        <p className="fine-print">
          Не удалось превратить в условие: {parsed.unmapped.join(", ")}. Эта
          часть запроса пока не влияет на выдачу.
        </p>
      )}
    </section>
  );
}
function SearchCard({
  hit,
  index,
  active,
  onHover,
  onEvidence,
}: {
  hit: SearchHit;
  index: number;
  active: boolean;
  onHover: (id: string | null) => void;
  onEvidence: (hotelId: string, v: Verdict) => void;
}) {
  const navigate = useNavigate();
  return (
    <article
      className={`result-card ${active ? "is-active" : ""}`}
      onMouseEnter={() => onHover(hit.hotel.id)}
      onFocus={() => onHover(hit.hotel.id)}
      data-testid={`hotel-card-${hit.hotel.id}`}
    >
      <HotelVisual hotel={hit.hotel} />
      <div className="result-card-body">
        <div className="result-meta">
          <span className="hotel-number">
            {String(index + 1).padStart(2, "0")}
          </span>
          <span>{hit.hotel.address}</span>
          <CompareButton hotelId={hit.hotel.id} />
        </div>
        <Link className="hotel-name" to={`/hotel/${hit.hotel.id}`}>
          <h3>{hit.hotel.name}</h3>
          <ArrowUpRight size={20} />
        </Link>
        <p className="result-summary">
          {hit.summary ||
            "Условия проверены по доступным фактам. Неизвестное требует уточнения."}
        </p>
        <VerdictList
          verdicts={hit.verdicts}
          hotelId={hit.hotel.id}
          onEvidence={(v) => onEvidence(hit.hotel.id, v)}
        />
        {hit.room_recommendation && (
          <div className="room-hint">
            <span>Ваш номер</span>
            <p>{hit.room_recommendation}</p>
          </div>
        )}
        {hit.disappointments.length > 0 && (
          <p className="disappointment">Учтите: {hit.disappointments[0]}</p>
        )}
        <div className="card-bottom">
          {hit.hotel.price_rub !== null ? (
            <span>
              <strong>{hit.hotel.price_rub.toLocaleString("ru")} ₽</strong> /
              ночь{" "}
              {hit.hotel.basic_fields_source === "mock_basic_card" && (
                <Synthetic>цена · макет</Synthetic>
              )}
            </span>
          ) : (
            <span className="muted">Цену уточнит отель</span>
          )}
          <button
            className="text-button"
            onClick={() => navigate(`/hotel/${hit.hotel.id}`)}
            data-testid={`open-hotel-${index}`}
          >
            Подробнее <ArrowRight size={16} />
          </button>
        </div>
      </div>
    </article>
  );
}
export function SearchPage() {
  const [params] = useSearchParams();
  const { setParsed, setSearchResult, status } = useApp();
  const q = params.get("q") || mashaQuery;
  const [request, setRequest] = useState<SearchRequest>({
    text: q,
    check_in: params.get("check_in"),
    check_out: params.get("check_out"),
    similar_to: params.get("similar_to"),
    limit: 40,
  });
  const resource = useResource(() => api.search(request), [request]);
  const [edited, setEdited] = useState<ParsedQuery | null>(null),
    [activeId, setActiveId] = useState<string | null>(null),
    [mapMobile, setMapMobile] = useState(false),
    [evidence, setEvidence] = useState<{
      detail: HotelDetail;
      fact: Fact;
    } | null>(null),
    [evidenceLoading, setEvidenceLoading] = useState(false),
    [evidenceError, setEvidenceError] = useState<Error | null>(null);
  const navigate = useNavigate();
  useEffect(() => {
    setRequest({
      text: q,
      check_in: params.get("check_in"),
      check_out: params.get("check_out"),
      similar_to: params.get("similar_to"),
      limit: 40,
    });
  }, [q, params]);
  useEffect(() => {
    if (resource.data) {
      setEdited(resource.data.parsed);
      setParsed(resource.data.parsed);
      setSearchResult(resource.data);
      setActiveId(resource.data.results[0]?.hotel.id || null);
    }
  }, [resource.data]);
  const showEvidence = async (hotelId: string, v: Verdict) => {
    setEvidenceLoading(true);
    setEvidenceError(null);
    try {
      const detail = await api.hotel(hotelId, edited);
      const fact =
        detail.facts.find((f) => v.fact_ids.includes(f.id)) ||
        detail.facts.find((f) => f.characteristic_id === v.characteristic_id);
      if (fact) setEvidence({ detail, fact });
      else
        setEvidenceError(
          new Error(
            "API не передал факт и доказательства для этого утверждения.",
          ),
        );
    } catch (e) {
      setEvidenceError(e as Error);
    } finally {
      setEvidenceLoading(false);
    }
  };
  const results = resource.data?.results || [];
  return (
    <div className="search-page">
      <div className="search-heading">
        <div>
          <Link className="back-link" to="/">
            ← Другой отдых
          </Link>
          <h1>
            Пусть всё <em>совпадёт.</em>
          </h1>
        </div>
        <div className="search-heading-meta">
          <span>
            <MapPin size={14} />
            Демо-регион: Сочи · центр + Адлер–Имеретинка ·{" "}
            {status.data?.hotel_count ?? "…"} отелей
          </span>
          <span>
            {request.check_in
              ? new Intl.DateTimeFormat("ru", {
                  day: "numeric",
                  month: "long",
                }).format(new Date(request.check_in))
              : "Даты не указаны"}
            {request.check_out
              ? ` — ${new Intl.DateTimeFormat("ru", { day: "numeric", month: "long" }).format(new Date(request.check_out))}`
              : ""}
          </span>
        </div>
      </div>
      <div className="search-split">
        <div className="search-list">
          <div className="query-strip">
            <Search size={19} />
            <span>{q}</span>
            <Link to="/" aria-label="Изменить запрос">
              <SlidersHorizontal size={17} />
            </Link>
          </div>
          {edited && (
            <CriteriaEditor
              parsed={edited}
              onChange={setEdited}
              busy={resource.loading}
              onApply={() =>
                setRequest({
                  ...request,
                  check_in: edited.check_in,
                  check_out: edited.check_out,
                  parsed: edited,
                })
              }
            />
          )}
          <button
            className="mobile-map-toggle button secondary"
            onClick={() => setMapMobile(!mapMobile)}
          >
            <MapPin size={16} />
            {mapMobile ? "Показать список" : "Показать карту"}
          </button>
          {resource.loading && (
            <Loading text="Сопоставляем ваши ожидания с фактами…" />
          )}
          <ErrorState error={resource.error} retry={resource.reload} />
          {resource.data?.parsed.clarifications.map((c) => (
            <div className="clarification" key={c}>
              <span>Нужно уточнить</span>
              <p>{c}</p>
              <Link to="/">
                Изменить запрос <ArrowRight size={14} />
              </Link>
            </div>
          ))}
          {!resource.loading && resource.data && !results.length && (
            <Empty
              title={
                resource.data.parsed.unmapped.includes("region_out_of_scope")
                  ? "Этот регион пока не собран"
                  : "Пока не нашлось совпадений"
              }
              action={
                <button
                  className="button secondary"
                  onClick={() => navigate("/")}
                >
                  Изменить запрос
                </button>
              }
            >
              {resource.data.parsed.unmapped.includes("region_out_of_scope")
                ? "В демо доступны центр Сочи и Адлер–Имеретинка. Отелей другого города в этой выдаче нет."
                : "Попробуйте сделать одно из условий желательным. Неизвестные характеристики не считаются подтверждёнными."}
            </Empty>
          )}
          {resource.data?.parsed.unmapped.includes("region_out_of_scope") && (
            <details className="region-limit" data-testid="region-limitation">
              <summary>Как расширить регион демо</summary>
              <p className="fine-print">
                Пайплайн параметризован конфигурацией региона:{" "}
                <code>make pipeline REGION=&lt;config&gt;</code>. Для другого
                города сначала нужно собрать и проверить данные.
              </p>
            </details>
          )}
          {(["all", "almost", "unknown"] as const).map((group) => {
            const hits = results.filter((r) => r.group === group);
            return (
              hits.length > 0 && (
                <section className="result-group" key={group}>
                  <div className="result-group-title">
                    <div>
                      <span className="eyebrow">
                        {group === "all"
                          ? "ВАШИ УСЛОВИЯ СОШЛИСЬ"
                          : group === "almost"
                            ? "ЕСТЬ КОМПРОМИСС"
                            : "НУЖНО УТОЧНИТЬ"}
                      </span>
                      <h2>
                        {group === "all"
                          ? "Подходят по обязательным"
                          : group === "almost"
                            ? "Почти то, что нужно"
                            : "Пока не всё известно"}
                      </h2>
                    </div>
                    <span className="result-count">{hits.length}</span>
                  </div>
                  {hits.map((hit) => (
                    <SearchCard
                      key={hit.hotel.id}
                      hit={hit}
                      index={results.indexOf(hit)}
                      active={activeId === hit.hotel.id}
                      onHover={setActiveId}
                      onEvidence={showEvidence}
                    />
                  ))}
                </section>
              )
            );
          })}
          {resource.data && (
            <p className="results-footnote">
              {resource.data.total} результатов ·{" "}
              {Math.round(resource.data.latency_ms)} мс · режим{" "}
              {resource.data.mode}. Наружный шум — приближённая модель, не
              измерение в номере.
            </p>
          )}
        </div>
        <aside
          className={`search-map ${mapMobile ? "mobile-open" : ""}`}
          aria-label="Карта результатов"
        >
          <TravelMap
            hotels={results.map((r) => r.hotel)}
            activeId={activeId}
            onHover={setActiveId}
            onSelect={(id) => navigate(`/hotel/${id}`)}
            month={edited?.month || 9}
          />
          <div className="map-under-note">
            <span>Карта объясняет, почему.</span>
            <p>
              Выберите отель — увидите его фасады, обзор и дорогу к морю, если
              они рассчитаны.
            </p>
          </div>
        </aside>
      </div>
      {evidence && (
        <EvidenceModal
          fact={evidence.fact}
          claims={evidence.detail.claims}
          onClose={() => setEvidence(null)}
        />
      )}{" "}
      {(evidenceLoading || evidenceError) && (
        <Modal
          title="Доказательства"
          onClose={() => {
            setEvidenceError(null);
            setEvidenceLoading(false);
          }}
        >
          {evidenceLoading ? <Loading /> : <ErrorState error={evidenceError} />}
        </Modal>
      )}
    </div>
  );
}
