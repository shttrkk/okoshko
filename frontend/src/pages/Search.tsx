import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type FormEvent,
} from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import {
  ArrowDown,
  ArrowRight,
  Footprints,
  Heart,
  Laptop,
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
import { api } from "../api";
import "../panel-r3-guest.css";
import {
  criterionBaseLabel,
  criterionLabel,
  criterionUnit,
  DEMO_BANNER,
  realHotelier,
  isDemoAnswer,
  guestText,
  humanExplanation,
  verdictCaveat,
} from "../components/panel-guest-presentation";
import { useApp } from "../state";
import { TravelMap } from "../components/Map";
import { GroupPlan } from "../components/GroupPlan";
import { RoomCriteriaEditor } from "../components/RoomCriteriaEditor";
import {
  chooseSuggestion,
  QuerySuggestions,
  roomRequestReason,
  useGuestSearch,
  requestedCriteria,
  mobilityContext,
  contextualDisappointment,
} from "../components/panel-guest-query";
import { RoomSuggestion } from "../components/RoomProfiles";
import { roomEvidenceScope } from "../components/group";
import {
  CompareButton,
  Empty,
  ErrorState,
  EvidenceModal,
  ContextEvidence,
  HotelVisual,
  Art,
  Loading,
  Modal,
  scopeText,
  Synthetic,
  VerdictList,
} from "../components/ui";
import type {
  Criterion,
  Fact,
  HotelDetail,
  ParsedQuery,
  RoomRequest,
  SearchHit,
  SearchRequest,
  Verdict,
} from "../types";
export const mashaQuery = "тихий номер с видом на закат недалеко от моря";
function criterionKey(c: Criterion) {
  const scope = c.scope;
  return JSON.stringify([
    c.characteristic_id,
    c.field,
    c.op,
    c.value,
    scope?.facade ?? null,
    scope?.floor_min ?? null,
    scope?.floor_max ?? null,
    scope?.month ?? null,
    scope?.room_type ?? null,
    !!c.no_stairs,
    !!c.no_crossings,
  ]);
}
function tripDates(checkIn: string | null, checkOut: string | null) {
  if (!checkIn || !checkOut) return checkIn || checkOut || "Даты не указаны";
  const a = new Date(checkIn),
    b = new Date(checkOut);
  const day = (d: Date) => d.getUTCDate();
  const genitive = (d: Date) =>
    new Intl.DateTimeFormat("ru", {
      day: "numeric",
      month: "long",
      timeZone: "UTC",
    })
      .format(d)
      .replace(/^\d+\s/, "");
  return a.getUTCMonth() === b.getUTCMonth()
    ? `${day(a)}–${day(b)} ${genitive(b)}`
    : `${day(a)} ${genitive(a)} — ${day(b)} ${genitive(b)}`;
}
function exampleHref(query: string) {
  const params = new URLSearchParams({ q: query });
  const checkIn = sessionStorage.getItem("okno-example-check-in"),
    checkOut = sessionStorage.getItem("okno-example-check-out");
  if (checkIn) params.set("check_in", checkIn);
  if (checkOut) params.set("check_out", checkOut);
  return `/search?${params}`;
}
const exampleQueries = [
  mashaQuery,
  "с ребёнком и коляской, до пляжа без лестниц",
  "вид на горы из окна",
  "круглосуточная аптека рядом",
  "в октябре ещё можно купаться",
];
const personas = [
  {
    icon: Moon,
    image: "persona-sleep",
    tone: "night",
    title: "Тихий номер, чтобы выспаться",
    text: "Тишина ночью и хорошая шумоизоляция окон",
    query: "тихий номер с хорошей шумоизоляцией",
  },
  {
    icon: Waves,
    image: "persona-sea-view",
    tone: "sea",
    title: "Номер с видом на море",
    text: "Вид на море с нужного этажа и пляж в паре шагов",
    query: "вид на море из окна и пляж рядом",
  },
  {
    icon: Footprints,
    image: "persona-family",
    tone: "green",
    title: "С ребёнком и коляской",
    text: "До пляжа без лестниц и сложных переходов",
    query:
      "до пляжа с коляской без лестниц и перехода трассы, рядом детская площадка",
  },
  {
    icon: Laptop,
    image: "persona-work",
    tone: "sky",
    title: "Работать у моря",
    text: "Связь для созвонов, тишина для мыслей",
    query: "стабильный интернет не меньше 30 Мбит, тихо днём, рядом море",
  },
  {
    icon: PawPrint,
    image: "persona-car-dog",
    tone: "coral",
    title: "На машине с собакой",
    text: "Парковка рядом и место для прогулок",
    query: "с собакой 30 кг, парковка у отеля, рядом где выгулять",
  },
  {
    icon: Heart,
    image: "persona-evening",
    tone: "sun",
    title: "В центре событий",
    text: "Кафе, вечерние прогулки и живая набережная",
    query: "кафе и бары рядом, в центре событий, недалеко от набережной",
  },
];
const promises = [
  {
    image: "compare",
    title: "Сравниваем номера, а не отели",
    text: "Фасад, этаж и месяц решают. Каждый факт раскрывается до источника, метода и даты.",
    link: "/compare",
    action: "Сравнить отели",
  },
  {
    image: "ask-hotel",
    title: "Чего не знаем — спросим",
    text: "«?» значит, что данных пока нет. Вопрос уйдёт отелю, а ответ увидят все гости.",
    link: "/hotelier",
    action: "Кабинет отеля",
  },
  {
    image: "passport",
    title: "Паспорт ожиданий",
    text: "При бронировании фиксируем, что для вас важно, и с чем согласился отель.",
    link: `/search?q=${encodeURIComponent(mashaQuery)}`,
    action: "Подобрать и забронировать",
  },
  {
    image: "changes",
    title: "Следим за переменами",
    text: "Построили дом напротив или закрыли пляж — предупредим до заезда.",
    link: "/changes",
    action: "Что изменилось",
  },
];
export function SearchForm({
  initial = "",
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
    [checkIn, setCheckIn] = useState(
      () => sessionStorage.getItem("okno-example-check-in") || "",
    ),
    [checkOut, setCheckOut] = useState(
      () => sessionStorage.getItem("okno-example-check-out") || "",
    ),
    [similarId, setSimilarId] = useState(""),
    [isSimilar, setIsSimilar] = useState(similar),
    [companyHelp, setCompanyHelp] = useState(false),
    [previousText, setPreviousText] = useState<string | null>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    if (!textarea.current) return;
    textarea.current.style.height = "auto";
    textarea.current.style.height = `${textarea.current.scrollHeight}px`;
  }, [text, companyHelp]);
  const mashaExample = () => {
    if (!text.trim()) return runSearch(mashaQuery);
    if (text === mashaQuery) return textarea.current?.focus();
    setPreviousText(text);
    setText(mashaQuery);
    textarea.current?.focus();
  };
  const runSearch = (query: string) => {
    setText(query);
    const params = new URLSearchParams({ q: query });
    if (checkIn) params.set("check_in", checkIn);
    if (checkOut) params.set("check_out", checkOut);
    if (isSimilar && similarId) params.set("similar_to", similarId);
    navigate(`/search?${params}`);
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    runSearch(text);
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
          ref={textarea}
          value={text}
          onChange={(e) => setText(e.target.value)}
          maxLength={2000}
          placeholder={
            companyHelp
              ? "Едем компанией: … — …; … — …. Для всех — …"
              : "Тихий номер с видом на закат недалеко от моря…"
          }
          rows={compact ? 2 : 3}
          data-testid="search-input"
          required={!isSimilar}
        />
      </label>
      {!compact && (
        <div className="search-example-chips">
          <button
            type="button"
            className="button secondary small"
            onClick={mashaExample}
            data-testid="masha-example"
            title={`Подставить демо-запрос: «${mashaQuery}»`}
          >
            Демо-запрос
          </button>
          <button
            type="button"
            className="text-button small group-search-example"
            data-testid="group-search-example"
            onClick={() => {
              setCompanyHelp((open) => !open);
              textarea.current?.focus();
            }}
            aria-expanded={companyHelp}
            aria-controls="company-search-help"
          >
            Едем компанией? Опишите каждого
          </button>
        </div>
      )}
      {companyHelp && (
        <p
          id="company-search-help"
          className="fine-print"
          data-testid="company-search-help"
        >
          Укажите свои пожелания по образцу: «Едем компанией: … — …; … — …. Для
          всех — …». Эта подсказка не добавляется в ваш запрос.
        </p>
      )}
      {previousText !== null && (
        <p className="fine-print" role="status">
          Демо-запрос вставлен. Поиск начнётся по кнопке «Найти мой отель».{" "}
          <button
            type="button"
            className="text-button small"
            data-testid="restore-search-input"
            onClick={() => {
              setText(previousText);
              setPreviousText(null);
              textarea.current?.focus();
            }}
          >
            Вернуть мой текст
          </button>
        </p>
      )}
      <div className="search-form-bottom">
        <div className="date-inputs">
          <label>
            <span>Заезд</span>
            <input
              aria-label="Дата заезда"
              type="date"
              value={checkIn}
              onChange={(e) => {
                setCheckIn(e.target.value);
                sessionStorage.setItem("okno-example-check-in", e.target.value);
              }}
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
              onChange={(e) => {
                setCheckOut(e.target.value);
                sessionStorage.setItem(
                  "okno-example-check-out",
                  e.target.value,
                );
              }}
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
            <span className="hero-line hero-sea">Ваше море.</span>
            <span className="hero-line hero-quiet">Ваша тишина.</span>
            <em className="hero-line">Ваше окно.</em>
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
        <div className="hero-art">
          <img
            src="/illustrations/hero.webp"
            alt="Побережье Сочи: отель, пальмы и дорожка к пляжу"
            width={1600}
            height={1200}
            fetchPriority="high"
          />
          <span className="hero-chip hero-chip-sea">
            <Waves size={16} /> Вид на море · с 5-го этажа
          </span>
          <span className="hero-chip hero-chip-walk">
            <Footprints size={16} /> До пляжа 7 мин пешком
          </span>
          <span className="hero-chip hero-chip-quiet">
            <Moon size={16} /> Тихо ночью: окна во двор
          </span>
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
            <span className="eyebrow">ГОТОВЫЕ ЗАПРОСЫ</span>
            <h2>Не знаете, с чего начать?</h2>
          </div>
          <ArrowDown size={23} />
        </div>
        <div className="scenario-grid">
          {personas.map((p, i) => (
            <button
              key={p.title}
              className={`scenario-card tone-${p.tone}`}
              style={{ "--i": i } as CSSProperties}
              onClick={() => navigate(exampleHref(p.query))}
            >
              <span className="scenario-art">
                <img
                  src={`/illustrations/${p.image}.webp`}
                  alt=""
                  loading="lazy"
                  width={1200}
                  height={900}
                />
              </span>
              <span className="scenario-body">
                <span className="scenario-icon">
                  <p.icon size={19} strokeWidth={1.8} />
                </span>
                <h3>{p.title}</h3>
                <p>{p.text}</p>
                <span className="scenario-cta">Подобрать отели →</span>
                <ArrowRight className="scenario-arrow" size={20} />
              </span>
            </button>
          ))}
        </div>
      </section>
      <section className="promise-section">
        <div className="promise-intro">
          <span className="eyebrow">ЗНАТЬ ДО ЗАЕЗДА</span>
          <h2>
            «Вид на море» —<br />
            это ещё не вся картина.
          </h2>
          <p>
            Какой фасад? С какого этажа? Не шумит ли улица ночью? Сколько идти
            обратно с пляжа? Что нельзя рассчитать — спросим у отеля.
          </p>
          <Link to="/sources" className="text-button">
            Как устроено О!кно <ArrowRight size={17} />
          </Link>
        </div>
        <div className="promise-grid">
          {promises.map((item, i) => (
            <Link
              key={item.title}
              to={item.link}
              className="promise-card"
              style={{ "--i": i } as CSSProperties}
            >
              <img
                src={`/illustrations/${item.image}.webp`}
                alt=""
                loading="lazy"
                width={1200}
                height={900}
              />
              <h3>{item.title}</h3>
              <p>{item.text}</p>
              <span className="promise-action">
                {item.action} <ArrowRight size={15} />
              </span>
            </Link>
          ))}
        </div>
      </section>
      <section className="region-section">
        <div className="region-copy">
          <span className="eyebrow">ГДЕ ИЩЕМ</span>
          <h2>Сочи: центр и Адлер — Имеретинка</h2>
          <p>
            {status.data?.hotel_count ?? hotels.data?.length ?? "…"} отелей. Для
            каждого по открытым данным посчитаны фасады, солнце, шум и дорога к
            морю.
          </p>
          <Link to="/sources" className="text-button">
            Данные и источники <ArrowRight size={17} />
          </Link>
        </div>
        <div className="hero-map-wrap">
          <TravelMap hero hotels={hotels.data || []} />
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
      <section className="home-data-note">
        <span className="connection-dot connected" />
        {status.data
          ? `${status.data.hotel_count ?? hotels.data?.length ?? "…"} отелей · характеристик в каталоге: ${status.data.characteristic_count ?? "…"}`
          : "Подключаем каталог реальных отелей"}
        <Link to="/quality">
          Как проверяем качество <ArrowRight size={13} />
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
    index: number;
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
  const move = (group: "must" | "nice", index: number) => {
    const target = group === "must" ? "nice" : "must",
      criterion = parsed[group][index];
    if (criterion)
      onChange({
        ...parsed,
        [group]: parsed[group].filter((_, i) => i !== index),
        [target]: [...parsed[target], criterion],
      });
  };
  const edit = (
    group: "must" | "nice",
    index: number,
    change: Partial<Criterion>,
  ) =>
    onChange({
      ...parsed,
      [group]: parsed[group].map((c, i) =>
        i === index
          ? { ...c, ...change, label: criterionLabel({ ...c, ...change }) }
          : c,
      ),
    });
  const drop = (group: "must" | "nice") => {
    if (drag && drag.group !== group) move(drag.group, drag.index);
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
              <strong>{tripDates(parsed.check_in, parsed.check_out)}</strong>
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
      {!!parsed.rooms?.length && (
        <h3 className="criteria-shared-heading">Для всех</h3>
      )}
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
            {parsed[group].map((c, index) => (
              <div
                className={`criterion-chip ${group}`}
                draggable
                onDragStart={() => setDrag({ index, group })}
                key={`${c.characteristic_id}:${c.field}:${c.op}:${index}`}
              >
                <span>
                  {typeof c.value === "number"
                    ? criterionBaseLabel(c)
                    : c.label}
                </span>
                {c.no_stairs &&
                  (typeof c.value === "number" ||
                    !/лестниц|ступен/i.test(c.label)) && (
                    <span className="chip-flag">Без лестниц</span>
                  )}
                {c.no_crossings &&
                  (typeof c.value === "number" ||
                    !/переход/i.test(c.label)) && (
                    <span className="chip-flag">Без переходов</span>
                  )}
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
                        edit(group, index, {
                          value: Number(e.target.value),
                        })
                      }
                    />
                    <span>{criterionUnit(c)}</span>
                  </label>
                )}
                <button
                  title={
                    group === "must"
                      ? "Сделать желательным"
                      : "Сделать обязательным"
                  }
                  aria-label={`Переместить ${c.label} в ${group === "must" ? "желательные" : "обязательные"}`}
                  onClick={() => move(group, index)}
                >
                  {group === "must" ? "↓" : "↑"}
                </button>
                <button
                  aria-label={`Убрать ${c.label}`}
                  onClick={() =>
                    onChange({
                      ...parsed,
                      [group]: parsed[group].filter((_, i) => i !== index),
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
                ![
                  ...parsed.must,
                  ...parsed.nice,
                  ...(parsed.no_data || []),
                ].some((c) => c.characteristic_id === s.id),
            )
            .map((s) => (
              <option key={s.id} value={s.id}>
                {s.name_ru}
              </option>
            ))}
        </select>
        {!parsed.rooms?.length && (
          <button
            className="text-button"
            onClick={onApply}
            disabled={busy || dateError}
          >
            Применить <ArrowRight size={15} />
          </button>
        )}
      </div>
      {(parsed.similar_to_name || !!parsed.no_data?.length) && (
        <div className="criteria-extra">
          {parsed.similar_to_name && (
            <span className="criterion-chip similar-chip">
              Похож на «{parsed.similar_to_name}»
            </span>
          )}
          {parsed.no_data?.map((c, index) => {
            const hard = (parsed.no_data_must || []).some(
              (item) => criterionKey(item) === criterionKey(c),
            );
            return (
              <span
                className="criterion-chip no-data-chip"
                key={`${criterionKey(c)}:${index}`}
              >
                <span>
                  {c.label} · {hard ? "обязательно · " : "желательно · "}нет
                  данных в регионе
                </span>
                <button
                  aria-label={`Сделать ${c.label} ${hard ? "желательным" : "обязательным"}`}
                  title={hard ? "Сделать желательным" : "Сделать обязательным"}
                  onClick={() =>
                    onChange({
                      ...parsed,
                      no_data_must: hard
                        ? (parsed.no_data_must || []).filter(
                            (item) => criterionKey(item) !== criterionKey(c),
                          )
                        : [...(parsed.no_data_must || []), c],
                    })
                  }
                >
                  {hard ? "↓" : "↑"}
                </button>
                <button
                  aria-label={`Убрать ${c.label}`}
                  onClick={() =>
                    onChange({
                      ...parsed,
                      no_data: parsed.no_data?.filter((_, i) => i !== index),
                      no_data_must: (parsed.no_data_must || []).filter(
                        (item) => criterionKey(item) !== criterionKey(c),
                      ),
                    })
                  }
                >
                  <X size={12} />
                </button>
              </span>
            );
          })}
        </div>
      )}
      {(() => {
        // The region marker is explained by the empty state, not as a phrase.
        const phrases = parsed.unmapped.filter(
          (u) => u !== "region_out_of_scope",
        );
        return phrases.length > 0 ? (
          <p className="fine-print">
            Не удалось превратить в условие: {phrases.join(", ")}. Эта часть
            запроса пока не влияет на выдачу.
          </p>
        ) : null;
      })()}
      <QuerySuggestions
        suggestions={parsed.suggestions}
        onChoose={(suggestion) =>
          onChange(chooseSuggestion(parsed, suggestion))
        }
      />
      {parsed.rooms?.map((room, index) => (
        <RoomCriteriaEditor
          key={index}
          room={room}
          index={index}
          onChange={(updated) =>
            onChange({
              ...parsed,
              rooms: parsed.rooms?.map((item, i) =>
                i === index ? updated : item,
              ),
            })
          }
        />
      ))}
      {!!parsed.rooms?.length && (
        <div className="criteria-apply">
          <button
            className="button secondary small"
            onClick={onApply}
            disabled={busy || dateError}
          >
            Применить <ArrowRight size={15} />
          </button>
        </div>
      )}
    </section>
  );
}
function distinctRankingReasons(reasons: string[] = []) {
  const seen = new Set<string>();
  return reasons
    .filter((reason) => {
      const body = reason
        .trim()
        .replace(/^«.*?»:\s*/, "")
        .replace(/\s+/g, " ")
        .toLocaleLowerCase("ru")
        .replaceAll("ё", "е");
      // Different reference names must not repeat the same generic explanation.
      const key =
        body.startsWith("выше совокупная оценка выполнения условий") ||
        body.startsWith("по условиям у него данные надежнее")
          ? "score"
          : body.startsWith("оценки совпадают;")
            ? "tie"
            : body.replace(/[.!?]+$/, "");
      if (!key || seen.has(key)) return false;
      seen.add(key);
      return true;
    })
    .slice(0, 3);
}
function rankingReasonParts(reason: string) {
  const beforeDetails = reason.split(";", 1)[0].trim();
  // Older API responses embedded the exact scores before the separator.
  const human = beforeDetails
    .replace(/\s*\(\s*\d+(?:[.,]\d+)?\s+против\s+\d+(?:[.,]\d+)?\s*\)/u, "")
    .trim();
  return {
    human,
    hasCalculation: reason.includes(";") || human !== beforeDetails,
  };
}
export function requiredStatusCounts(hit: SearchHit) {
  const counts = { yes: 0, partial: 0, no: 0, unknown: 0 };
  for (const verdict of [
    ...hit.verdicts,
    ...(hit.room_plan || []).flatMap((item) => item.verdicts),
  ]) {
    if (verdict.required) counts[verdict.status] += 1;
  }
  // Some no-data/scope requirements have no verdict in legacy responses.
  counts.unknown += Math.max(
    0,
    hit.must_total -
      Object.values(counts).reduce((sum, count) => sum + count, 0),
  );
  return counts;
}
function requiredLabels(hit: SearchHit) {
  const counts = requiredStatusCounts(hit);
  const required = [
    ...hit.verdicts,
    ...(hit.room_plan || []).flatMap((item) => item.verdicts),
  ].filter((v) => v.required);
  const confirmed = required.filter(realHotelier).length;
  const demo = required.filter(
    (v) => isDemoAnswer(v) && v.status === "yes",
  ).length;
  const demoCalculation = required.filter(
    (v) => v.synthetic && !isDemoAnswer(v) && v.status === "yes",
  ).length;
  return {
    counts,
    labels: {
      yes: [
        confirmed && `${confirmed} подтвердил отель`,
        demo && `${demo} ответ отеля · демо`,
        demoCalculation && `${demoCalculation} демо-расчёт`,
        counts.yes - confirmed - demo - demoCalculation > 0 &&
          `${counts.yes - confirmed - demo - demoCalculation} по расчёту`,
      ]
        .filter(Boolean)
        .join(" · "),
      partial: `${counts.partial} примерно`,
      no: `${counts.no} не подходит`,
      unknown: `${counts.unknown} мало данных`,
    },
  };
}
export function requiredStatusLabel(hit: SearchHit) {
  const { counts, labels } = requiredLabels(hit);
  return Object.values(counts).some(Boolean)
    ? `Обязательные: ${(["yes", "partial", "no", "unknown"] as const)
        .filter((status) => counts[status])
        .map((status) => labels[status])
        .join(", ")}`
    : "Обязательных условий нет";
}
function RequiredStatusSummary({ hit }: { hit: SearchHit }) {
  const { counts, labels } = requiredLabels(hit);
  const total = Object.values(counts).reduce((sum, count) => sum + count, 0);
  return (
    <div
      className="required-summary"
      role="group"
      aria-label={requiredStatusLabel(hit)}
      data-testid="required-summary"
    >
      <span className="required-summary-label">
        {total ? "Обязательные" : "Обязательных условий нет"}
      </span>
      {total > 0 && (
        <ul
          className="required-status-list"
          aria-label="Статусы обязательных условий"
        >
          {(["yes", "partial", "no", "unknown"] as const)
            .filter((status) => counts[status] > 0)
            .map((status) => (
              <li
                key={status}
                className={`required-status ${status === "yes" ? "partial" : status}`}
                data-testid="required-status"
                data-status={status}
                data-count={counts[status]}
              >
                {labels[status]}
              </li>
            ))}
        </ul>
      )}
    </div>
  );
}
function SearchCard({
  hit,
  month,
  roomRequests,
  roomReason,
  requestedIds,
  withMobility,
  index,
  firstResultId,
  active,
  hovered,
  onHover,
  onLocate,
  onEvidence,
}: {
  hit: SearchHit;
  month: number;
  roomRequests?: RoomRequest[];
  roomReason: string;
  requestedIds: string[];
  withMobility: boolean;
  index: number;
  firstResultId?: string;
  active: boolean;
  hovered: boolean;
  onHover: (id: string | null) => void;
  onLocate: (id: string) => void;
  onEvidence: (hotelId: string, v: Verdict) => void;
}) {
  const navigate = useNavigate();
  const visibleVerdicts = requestedIds.length
    ? hit.verdicts.filter((verdict) =>
        requestedIds.includes(verdict.characteristic_id),
      )
    : hit.verdicts;
  const reasons =
    index > 0 && index < 4 && hit.hotel.id !== firstResultId
      ? distinctRankingReasons(hit.why_not_higher)
      : [];
  return (
    <article
      className={`result-card ${active ? "is-active" : ""} ${hovered ? "is-hovered" : ""}`}
      onMouseEnter={() => onHover(hit.hotel.id)}
      onMouseLeave={() => onHover(null)}
      data-testid={`hotel-card-${hit.hotel.id}`}
    >
      <HotelVisual hotel={hit.hotel} fallbackLink />
      <div className="result-card-body">
        <div className="result-meta">
          <span className="hotel-number">
            {String(index + 1).padStart(2, "0")}
          </span>
          <span>{hit.hotel.address}</span>
          <button
            className={`text-button small locate-button ${active ? "active" : ""}`}
            onClick={() => onLocate(hit.hotel.id)}
            aria-pressed={active}
          >
            <MapPin size={14} />
            {active ? "На карте" : "Показать на карте"}
          </button>
          <CompareButton hotelId={hit.hotel.id} />
        </div>
        <Link className="hotel-name" to={`/hotel/${hit.hotel.id}`}>
          <h3>{hit.hotel.name}</h3>
          <ArrowRight size={20} />
        </Link>
        {"why_higher" in hit &&
          typeof hit.why_higher === "string" &&
          hit.why_higher && (
            <p className="fine-print" data-testid="why-higher">
              {hit.why_higher}
            </p>
          )}
        <div className="result-summary">
          <RequiredStatusSummary hit={{ ...hit, verdicts: visibleVerdicts }} />
          {hit.room_profile?.scope.facade && !hit.room_recommendation && (
            <span>{scopeText(roomEvidenceScope(hit.room_profile.scope))}</span>
          )}
        </div>
        <VerdictList
          compact
          verdicts={visibleVerdicts}
          hotelId={hit.hotel.id}
          onEvidence={(v) => onEvidence(hit.hotel.id, v)}
        />
        {hit.room_plan?.length ? (
          <GroupPlan
            hotelId={hit.hotel.id}
            unknownFloors={hit.hotel.floors === null}
            items={hit.room_plan}
            requests={roomRequests}
            compact
          />
        ) : (
          roomReason &&
          hit.room_recommendation && (
            <RoomSuggestion
              hotelId={hit.hotel.id}
              unknownFloors={hit.hotel.floors === null}
              recommendation={hit.room_recommendation}
              reason={roomReason}
              requestedIds={requestedIds}
              profile={hit.room_profile}
              month={hit.room_profile?.scope.month || month}
            />
          )
        )}
        {(hit.summary ||
          hit.disappointments.length > 0 ||
          reasons.length > 0) && (
          <details
            className="verdict-caveats result-explanation"
            data-testid="card-details"
          >
            <summary>Почему так</summary>
            {hit.summary && (
              <p>
                {humanExplanation(
                  hit.summary,
                  hit.verdicts.some((v) => v.synthetic),
                  1,
                )}
                {hit.verdicts.some(
                  (verdict) =>
                    verdict.status === "yes" && isDemoAnswer(verdict),
                ) && " Основание: ответ отеля · демо."}
              </p>
            )}
            {hit.disappointments.length > 0 && (
              <p>
                {humanExplanation(
                  contextualDisappointment(
                    hit.disappointments[0],
                    withMobility,
                  ),
                )}
              </p>
            )}
            <details className="room-caveats-details" data-testid="card-proof">
              <summary>Для дотошных</summary>
              {hit.summary && (
                <p>
                  {guestText(
                    hit.summary,
                    hit.verdicts.some((v) => v.synthetic),
                  )}
                </p>
              )}
              {hit.disappointments.length > 0 && (
                <ul>
                  {hit.disappointments.map((text, index) => (
                    <li key={index}>
                      {guestText(contextualDisappointment(text, withMobility))}
                    </li>
                  ))}
                </ul>
              )}
              {hit.verdicts.map((verdict, index) => (
                <div key={index}>
                  <p>
                    {guestText(verdict.text, verdict.synthetic)}{" "}
                    {verdictCaveat(verdict)}
                  </p>
                  {!!verdict.context_fact_ids?.length && (
                    <ContextEvidence
                      hotelId={hit.hotel.id}
                      factIds={verdict.context_fact_ids}
                    />
                  )}
                </div>
              ))}
              {reasons.length > 0 && (
                <details data-testid={`search-reasons-${hit.hotel.id}`}>
                  <summary aria-label={`Почему не выше: ${hit.hotel.name}`}>
                    Почему не выше
                  </summary>
                  {reasons.map((reason) => {
                    const { human, hasCalculation } =
                      rankingReasonParts(reason);
                    return (
                      <div key={reason} data-testid="ranking-reason">
                        <p>{human}</p>
                        {hasCalculation && (
                          <details
                            className="room-caveats-details"
                            data-testid="ranking-calculation"
                          >
                            <summary>Расчёт сравнения</summary>
                            <p>
                              {guestText(
                                reason,
                                hit.verdicts.some((v) => v.synthetic),
                              )}
                            </p>
                          </details>
                        )}
                      </div>
                    );
                  })}
                </details>
              )}
            </details>
          </details>
        )}
        <div className="card-bottom">
          {hit.hotel.price_rub !== null ? (
            <span>
              <strong>{hit.hotel.price_rub.toLocaleString("ru")} ₽</strong> /
              ночь{" "}
              {hit.hotel.basic_fields_source === "mock_basic_card" && (
                <Synthetic>Цена · демо-данные</Synthetic>
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
            Страница отеля <ArrowRight size={16} />
          </button>
        </div>
      </div>
    </article>
  );
}
export function SearchPage() {
  const [params] = useSearchParams();
  const { parsed: savedParsed, setParsed, setSearchResult, status } = useApp();
  const q = params.get("q") || mashaQuery;
  const [request, setRequest] = useState<SearchRequest>({
    text: q,
    parsed:
      params.has("edit") && savedParsed?.text === q ? savedParsed : undefined,
    check_in: params.get("check_in"),
    check_out: params.get("check_out"),
    similar_to: params.get("similar_to"),
    limit: 40,
  });
  const resource = useGuestSearch(request);
  const [edited, setEdited] = useState<ParsedQuery | null>(null),
    [activeId, setActiveId] = useState<string | null>(null),
    [hoverId, setHoverId] = useState<string | null>(null),
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
      parsed:
        params.has("edit") && savedParsed?.text === q ? savedParsed : undefined,
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
  // Without a single recognised condition the list is a browse, not a match.
  const noCriteria =
    !!resource.data &&
    !resource.data.parsed.must.length &&
    !resource.data.parsed.nice.length &&
    !resource.data.parsed.no_data_must?.length &&
    !resource.data.parsed.rooms?.length &&
    !resource.data.parsed.similar_to;
  const groups = ["all", "almost", "unknown", "browse", "excluded"] as const;
  const groupOf = (hit: SearchHit) => {
    const requiredNo = [
      ...hit.verdicts,
      ...(hit.room_plan || []).flatMap((item) => item.verdicts),
    ].some(
      (verdict) =>
        verdict.required && verdict.status === "no" && !verdict.disputed,
    );
    return requiredNo
      ? "excluded"
      : noCriteria && hit.group === "all"
        ? "browse"
        : hit.group;
  };
  const firstResultId = groups
    .map((group) => results.find((hit) => groupOf(hit) === group)?.hotel.id)
    .find(Boolean);
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
            {tripDates(
              (resource.data?.parsed ?? request).check_in ?? null,
              (resource.data?.parsed ?? request).check_out ?? null,
            )}
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
          {!!resource.data?.parsed.no_data_must?.length && (
            <div
              className="clarification"
              role="status"
              data-testid="missing-required-data"
            >
              <strong>Полное совпадение нельзя подтвердить</strong>
              <p>
                Для обязательных условий пока нет данных:{" "}
                {resource.data.parsed.no_data_must
                  .map((c) => c.label)
                  .join(", ")}
                . Ни один отель не считается совпавшим по всем обязательным
                условиям. Можно уточнить эти сведения у отеля или сделать
                условие желательным.
              </p>
            </div>
          )}
          {!noCriteria &&
            (() => {
              // No-data and unmapped notes are already shown as chips and
              // fine print in the criteria panel; keep only real questions.
              const notes = (resource.data?.parsed.clarifications || []).filter(
                (c) =>
                  !c.startsWith("В демо-регионе нет данных") &&
                  !c.startsWith("Не удалось сопоставить с каталогом"),
              );
              return notes.length ? (
                <div className="clarification">
                  <span>Нужно уточнить</span>
                  {notes.map((c) => (
                    <p key={c}>{c}</p>
                  ))}
                  <Link to="/">
                    Изменить запрос <ArrowRight size={14} />
                  </Link>
                </div>
              ) : null;
            })()}
          {!resource.loading && resource.data && !results.length && (
            <Empty
              art="empty-search"
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
          {noCriteria && results.length > 0 && (
            <div className="no-criteria" data-testid="no-criteria">
              <Art name="empty-search" className="no-criteria-art" />
              <strong>Пока подбираем без ваших условий</strong>
              <p>
                {resource.data?.parsed.clarifications[0] ||
                  "Не нашли в запросе условий из каталога. Опишите, что важно: вид, тишина, дорога к пляжу."}
              </p>
              <div className="example-queries">
                {exampleQueries.map((text) => (
                  <Link key={text} to={exampleHref(text)}>
                    {text}
                  </Link>
                ))}
              </div>
            </div>
          )}
          {results.some((hit) =>
            [
              ...hit.verdicts,
              ...(hit.room_plan || []).flatMap((item) => item.verdicts),
            ].some(isDemoAnswer),
          ) && (
            <p
              className="demo-results-banner"
              data-testid="demo-results-banner"
            >
              {DEMO_BANNER}
            </p>
          )}
          {groups.map((group) => {
            const hits = results.filter((r) => groupOf(r) === group);
            const hasPartial = hits.some((hit) =>
              [
                ...hit.verdicts,
                ...(hit.room_plan || []).flatMap((item) => item.verdicts),
              ].some((verdict) => verdict.status === "partial"),
            );
            const hasRequiredDemo =
              group === "all" &&
              hits.length > 0 &&
              hits.some((hit) => {
                const required = [
                  ...hit.verdicts,
                  ...(hit.room_plan || []).flatMap((item) => item.verdicts),
                ].filter((verdict) => verdict.required);
                return (
                  required.length > 0 &&
                  required.some(
                    (verdict) =>
                      verdict.status === "yes" && isDemoAnswer(verdict),
                  )
                );
              });
            const legendGroup = groups.find((name) =>
              results.some(
                (hit) =>
                  groupOf(hit) === name &&
                  [
                    ...hit.verdicts,
                    ...(hit.room_plan || []).flatMap((item) => item.verdicts),
                  ].some((v) => v.status === "partial"),
              ),
            );
            return (
              hits.length > 0 && (
                <section className="result-group" key={group}>
                  <div className="result-group-title">
                    <div>
                      <span className="eyebrow">
                        {
                          {
                            all: hasRequiredDemo
                              ? "СОВПАДЕНИЯ С ДЕМО-ОТВЕТАМИ"
                              : hasPartial
                                ? "ЕСТЬ ПРЕДВАРИТЕЛЬНЫЕ СОВПАДЕНИЯ"
                                : "ВАШИ УСЛОВИЯ СОШЛИСЬ",
                            almost: "ЕСТЬ КОМПРОМИСС",
                            unknown: "НУЖНО УТОЧНИТЬ",
                            browse: "ОТЕЛИ РЕГИОНА",
                            excluded: "ЕСТЬ НЕСОВПАДЕНИЕ",
                          }[group]
                        }
                      </span>
                      <h2>
                        {
                          {
                            all: hasRequiredDemo
                              ? "В совпадениях есть демо-ответы — подтвердите их у отеля"
                              : hasPartial
                                ? "Совпадения — с уточнениями"
                                : "Подходят по обязательным",
                            almost: "Почти то, что нужно",
                            unknown: "Пока не всё известно",
                            browse: "Без учёта ваших условий",
                            excluded:
                              "Есть обязательное условие, которое не подходит",
                          }[group]
                        }
                      </h2>
                    </div>
                    <span className="result-count">{hits.length}</span>
                  </div>
                  {hasPartial && group !== legendGroup && (
                    <p className="result-group-note">
                      {
                        {
                          all: "Некоторые совпадения пока неточные — проверьте значки «≈»",
                          almost:
                            "Здесь есть уступки по условиям и неточные данные",
                          unknown:
                            "Данных не хватает: часть ответов ещё нужно уточнить",
                          browse: "Некоторые пожелания ещё требуют уточнения",
                          excluded:
                            "По известным данным обязательное условие не выполняется",
                        }[group]
                      }
                    </p>
                  )}
                  {group === legendGroup && (
                    <p
                      className="result-group-note"
                      data-testid="partial-legend"
                    >
                      ≈ — расчёт или предварительные сведения. Источник указан
                      рядом.
                    </p>
                  )}
                  {hits.map((hit) => (
                    <SearchCard
                      key={hit.hotel.id}
                      hit={hit}
                      month={resource.data?.parsed.month || 9}
                      roomRequests={resource.data?.parsed.rooms}
                      withMobility={mobilityContext(resource.data?.parsed)}
                      requestedIds={requestedCriteria(
                        resource.data?.parsed,
                      ).map((criterion) => criterion.characteristic_id)}
                      roomReason={roomRequestReason(
                        resource.data?.parsed,
                        hit.verdicts,
                      )}
                      index={results.indexOf(hit)}
                      firstResultId={firstResultId}
                      active={activeId === hit.hotel.id}
                      hovered={hoverId === hit.hotel.id}
                      onHover={setHoverId}
                      onLocate={(id) => {
                        setActiveId(id);
                        if (window.matchMedia("(max-width: 680px)").matches)
                          setMapMobile(true);
                      }}
                      onEvidence={showEvidence}
                    />
                  ))}
                </section>
              )
            );
          })}
          {resource.data && (
            <p className="results-footnote">
              Показаны {results.length} лучших из {resource.data.total}. Шум
              ночью рассчитан приблизительно; внутри номера он может отличаться.
              Сторону, этаж и наличие номера подтвердит отель.
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
            hoverId={hoverId}
            onHover={setHoverId}
            onSelect={(id) => {
              setActiveId(id);
              document
                .querySelector(`[data-testid="hotel-card-${id}"]`)
                ?.scrollIntoView({ behavior: "smooth", block: "nearest" });
            }}
            onOpen={(id) => navigate(`/hotel/${id}`)}
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
