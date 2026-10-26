import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import {
  ArrowRight,
  Check,
  CircleHelp,
  ExternalLink,
  LoaderCircle,
  MessageCircle,
  RefreshCw,
  X,
} from "lucide-react";
import {
  api,
  ApiError,
  dateText,
  record,
  textValue,
  useResource,
} from "../api";
import {
  buildingModels,
  hotelPhotos,
  photoCredit,
  photoLabel,
  type HotelPhoto,
} from "./Photos";
import { humanFact, METHOD_NAMES, MONTHS, SOURCE_NAMES } from "./facts";
import { useApp } from "../state";
import type { Claim, Fact, Hotel, Scope, Verdict } from "../types";
export const names: Record<string, string> = {
  sea_view: "Вид на море",
  sunset_view: "Закат из окна",
  mountain_view: "Вид на горы",
  quiet_night: "Тишина ночью",
  beach_walk: "Дорога до пляжа",
  night_pharmacy: "Ночная аптека",
  family_nearby: "Для семьи",
  car_access: "На машине",
  climate: "Климат",
  soundproofing: "Шумоизоляция окон",
  room_orientation: "Сторона и этаж номера",
  pets: "С питомцем",
  air_quality: "Качество воздуха",
  connectivity: "Связь для работы",
  building_history: "История здания",
  nearby_photos: "Фото вокруг",
  morning_swim: "Купание до завтрака",
  nightlife: "В гуще событий",
  morning_sun: "Утреннее солнце",
  evening_heat: "Вечернее солнце и нагрев",
  railway_noise: "Железнодорожный шум",
  beach_surface: "Покрытие пляжа",
  accessible_route: "Доступность маршрута",
  green_nearby: "Зелень рядом",
};
const sides: Record<string, string> = {
  N: "север",
  NE: "северо-восток",
  E: "восток",
  SE: "юго-восток",
  S: "юг",
  SW: "юго-запад",
  W: "запад",
  NW: "северо-запад",
};
export function scopeText(scope: Scope) {
  return (
    [
      scope.facade &&
        `${sides[scope.facade]}${scope.type === "facade" ? " · фасад" : ""}`,
      scope.floor_min &&
        (scope.floor_max && scope.floor_max !== scope.floor_min
          ? `этажи ${scope.floor_min}–${scope.floor_max}`
          : scope.floor_max
            ? `${scope.floor_min}-й этаж`
            : `с ${scope.floor_min}-го этажа`),
      scope.room_type,
      scope.month && MONTHS[scope.month - 1],
    ]
      .filter(Boolean)
      .join(" · ") || "Весь отель"
  );
}
export function Loading({
  text = "Загружаем реальные данные…",
}: {
  text?: string;
}) {
  return (
    <div className="loading" role="status">
      <LoaderCircle className="spin" size={23} />
      <span>{text}</span>
    </div>
  );
}
export function Empty({
  title = "Пока нет данных",
  children,
  action,
}: {
  title?: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="empty">
      <CircleHelp size={28} />
      <h3>{title}</h3>
      {children && <p>{children}</p>}
      {action}
    </div>
  );
}
export function ErrorState({
  error,
  retry,
}: {
  error: Error | null;
  retry?: () => void;
}) {
  if (!error) return null;
  return (
    <div className="error-state" role="alert">
      <span className="eyebrow">
        {error instanceof ApiError
          ? `API · ${error.status}`
          : error.message === "Failed to fetch"
            ? "Соединение"
            : "Проверка"}
      </span>
      <h3>
        {error instanceof ApiError || error.message === "Failed to fetch"
          ? "Не удалось получить данные"
          : "Проверьте введённые данные"}
      </h3>
      <p>
        {error.message === "Failed to fetch"
          ? "Проверьте, запущен ли сервис. Страница готова повторить запрос."
          : error.message}
      </p>
      {retry && (
        <button className="button secondary small" onClick={retry}>
          <RefreshCw size={15} />
          Повторить
        </button>
      )}
    </div>
  );
}
export function PageIntro({
  eyebrow,
  title,
  children,
  action,
}: {
  eyebrow: string;
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="page-intro">
      <div>
        <div className="eyebrow">{eyebrow}</div>
        <h1>{title}</h1>
        {children && <p>{children}</p>}
      </div>
      {action}
    </div>
  );
}
export function Synthetic({
  children = "Демонстрация · синтетика",
}: {
  children?: ReactNode;
}) {
  return <span className="badge synthetic">{children}</span>;
}
export function Modal({
  title,
  children,
  onClose,
  wide = false,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  wide?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null),
    closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const old = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const focusSelector =
      'button:not([disabled]),a[href],input:not([disabled]),select:not([disabled]),textarea:not([disabled]),summary,[tabindex="0"]';
    (
      ref.current?.querySelector<HTMLElement>(focusSelector) || ref.current
    )?.focus();
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeRef.current();
      if (e.key === "Tab") {
        const els = ref.current?.querySelectorAll<HTMLElement>(focusSelector);
        if (!els?.length) return;
        const first = els[0],
          last = els[els.length - 1];
        if (
          !ref.current?.contains(document.activeElement) ||
          document.activeElement === ref.current
        ) {
          e.preventDefault();
          (e.shiftKey ? last : first).focus();
        } else if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", handler);
    return () => {
      document.body.style.overflow = old;
      document.removeEventListener("keydown", handler);
      previous?.focus();
    };
  }, []);
  return (
    <div
      className="modal-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className={`modal ${wide ? "wide" : ""}`}
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
      >
        <div className="modal-head">
          <h2>{title}</h2>
          <button
            className="icon-button"
            aria-label="Закрыть"
            onClick={onClose}
          >
            <X />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}
export function AskButton({
  hotelId,
  characteristicId,
  scope,
  compact = false,
}: {
  hotelId: string;
  characteristicId: string;
  scope?: Scope;
  compact?: boolean;
}) {
  const key = JSON.stringify([
    hotelId,
    characteristicId,
    scope
      ? [
          scope.type,
          scope.facade ?? null,
          scope.floor_min ?? null,
          scope.floor_max ?? null,
          scope.room_type ?? null,
          scope.month ?? null,
        ]
      : null,
  ]);
  const [requestState, setRequestState] = useState<{
    key: string;
    status: "sending" | "sent" | "error";
    error?: Error;
  } | null>(null);
  const current = requestState?.key === key ? requestState : null;
  const busy = current?.status === "sending";
  const sent = current?.status === "sent";
  const error = current?.error;
  const { toast } = useApp();
  const ask = async () => {
    setRequestState({ key, status: "sending" });
    try {
      await api.ask(hotelId, characteristicId, scope);
      setRequestState((old) =>
        old?.key === key ? { key, status: "sent" } : old,
      );
      toast("Уточнение добавлено к вопросу в кабинете");
    } catch (e) {
      setRequestState((old) =>
        old?.key === key ? { key, status: "error", error: e as Error } : old,
      );
    }
  };
  return (
    <span className="ask-wrap">
      <button
        className={compact ? "text-button small" : "button secondary small"}
        onClick={ask}
        disabled={busy || sent}
        data-testid={`ask-${characteristicId}`}
      >
        {busy ? (
          <LoaderCircle size={14} className="spin" />
        ) : sent ? (
          <Check size={14} />
        ) : (
          <MessageCircle size={14} />
        )}{" "}
        {sent ? "Добавлено к вопросу" : compact ? "Спросить" : "Спросить отель"}
      </button>
      {error && (
        <span role="alert" className="inline-error">
          {error.message}
        </span>
      )}
    </span>
  );
}
export function VerdictList({
  verdicts,
  hotelId,
  onEvidence,
  onFocusFact,
  questionScope,
  compact = false,
}: {
  verdicts: Verdict[];
  hotelId: string;
  onEvidence?: (v: Verdict) => void;
  onFocusFact?: (v: Verdict) => void;
  questionScope?: (v: Verdict) => Scope | undefined;
  /** Search cards: the gist only; caveats live on the hotel page. */
  compact?: boolean;
}) {
  return (
    <ul className={`verdict-list ${compact ? "compact" : ""}`}>
      {verdicts.map((v, i) => (
        <li key={`${v.characteristic_id}-${i}`}>
          <span
            className={`verdict-symbol ${v.disputed ? "disputed" : v.status}`}
            aria-label={
              v.disputed
                ? "Есть противоречие"
                : {
                    yes: "Подходит",
                    partial: "Частично",
                    no: "Не подходит",
                    unknown: "Неизвестно",
                  }[v.status]
            }
          >
            {v.disputed ? "!" : v.symbol}
          </span>
          <div>
            <button
              className="verdict-text"
              onClick={() => onEvidence?.(v)}
              disabled={!onEvidence}
            >
              <strong>{v.label}</strong>
              {v.disputed && (
                <span className="badge disputed-badge">Есть противоречие</span>
              )}
              {v.status === "unknown" && !v.disputed && (
                <span className="badge unknown-badge">Нет данных</span>
              )}
              {v.synthetic && <Synthetic>Подтверждение · синтетика</Synthetic>}
              <span>{v.text}</span>
              {v.caveat && !compact && (
                <small>{v.caveat.split(";")[0].slice(0, 220)}</small>
              )}
            </button>
            {!compact && onFocusFact && (
              <button
                className="text-button"
                data-testid={`verdict-fact-${v.characteristic_id}`}
                aria-controls={`fact-card-${v.characteristic_id}`}
                onClick={() => onFocusFact(v)}
              >
                К факту <ArrowRight size={12} />
              </button>
            )}
            {!compact &&
              v.caveat &&
              (v.caveat.includes(";") || v.caveat.length > 220) && (
                <details className="verdict-caveats">
                  <summary>Все оговорки</summary>
                  <p>{v.caveat}</p>
                </details>
              )}
            {v.status === "unknown" && (
              <AskButton
                compact
                hotelId={hotelId}
                characteristicId={v.characteristic_id}
                scope={questionScope?.(v)}
              />
            )}
          </div>
        </li>
      ))}
    </ul>
  );
}
export function HotelVisual({
  hotel,
  className = "",
  credit = false,
}: {
  hotel: Hotel;
  className?: string;
  credit?: boolean;
}) {
  const index = useResource(hotelPhotos);
  const models = useResource(buildingModels);
  const [failed, setFailed] = useState<string[]>([]);
  const own: HotelPhoto[] = hotel.image?.startsWith("/data/")
    ? [{ kind: "building", url: hotel.image, local: true }]
    : [];
  const photo = [...own, ...(index.data?.[hotel.id] || [])].find(
    (p) =>
      (p.local ? p.url.startsWith("/data/") : p.url.startsWith("https://")) &&
      !failed.includes(p.url),
  );
  return (
    <div className={`hotel-visual ${photo ? "has-photo" : ""} ${className}`}>
      {photo ? (
        <>
          <img
            src={photo.url}
            alt={`${hotel.name}: ${photo.kind === "building" ? "здание отеля" : "фото с сайта отеля"}`}
            loading="lazy"
            decoding="async"
            referrerPolicy="no-referrer"
            onError={() => setFailed((old) => [...old, photo.url])}
          />
          <span className="photo-credit" title={photoCredit(photo)}>
            {photoLabel(photo)}
            {credit && photo.page_url && (
              <a href={photo.page_url} target="_blank" rel="noreferrer">
                {photo.author ? `${photo.author} · ` : ""}
                {photo.license || "источник"} <ExternalLink size={11} />
              </a>
            )}
          </span>
        </>
      ) : models.data?.[hotel.id]?.url?.startsWith("/data/") &&
        !failed.includes(models.data[hotel.id].url) ? (
        <>
          <img
            src={models.data[hotel.id].url}
            alt={`${hotel.name}: здание на 3D-модели по открытым данным`}
            loading="lazy"
            decoding="async"
            onError={() =>
              setFailed((old) => [...old, models.data![hotel.id].url])
            }
          />
          <span
            className="photo-credit"
            title="Контур и этажность из OpenStreetMap/Overture на локальной карте. Здание отеля выделено цветом."
          >
            3D-модель по OSM · не фото
          </span>
        </>
      ) : (
        <>
          <div className="building-art">
            <i />
            <i />
            <i />
            <i />
            <i />
          </div>
          <span className="visual-caption">
            {hotel.address || "Черноморское побережье"}
            <br />
            <small>Схема · фото отеля пока нет</small>
          </span>
        </>
      )}
      {hotel.stars && <span className="stars">{"★".repeat(hotel.stars)}</span>}
    </div>
  );
}
export function needsQuestion(fact: Fact) {
  const value = record(fact.value);
  return (
    ["unknown", "disputed", "stale"].includes(fact.status) ||
    fact.value === null ||
    ("available" in value && value.available === null)
  );
}
export function FactValue({ fact }: { fact: Fact }) {
  const v = record(fact.value);
  if (fact.status === "disputed" && fact.value === null)
    return <span className="disputed-text">! Есть противоречие</span>;
  if (fact.status === "unknown" || fact.value === null)
    return <span className="unknown-text">? Нет данных</span>;
  const human = humanFact(fact);
  if (human)
    return (
      <span>
        {fact.status === "disputed" && (
          <strong className="disputed-text">Спорное значение · </strong>
        )}
        {human}
      </span>
    );
  const fields: Record<string, string> = {
    min_floor: "с этажа",
    facade: "фасад",
    minutes: "мин пешком",
    return_minutes: "мин обратно",
    distance_m: "м",
    night_db_min: "ночью от, дБ",
    night_db_max: "ночью до, дБ",
    water_c: "вода, °C",
    air_c: "воздух, °C",
    max_weight_kg: "до, кг",
    fee_rub_night: "₽ за ночь",
    download_mbps: "Мбит/с",
    european_aqi: "AQI",
    probability: "вероятность",
    rooms: "номера",
    windows: "окна",
    months: "месяцы",
  };
  const parts = Object.entries(v)
    .filter(
      ([key, val]) =>
        key !== "available" &&
        val !== null &&
        val !== undefined &&
        key in fields,
    )
    .map(
      ([key, val]) =>
        `${fields[key]}: ${key === "facade" ? sides[String(val)] || textValue(val) : Array.isArray(val) ? val.join(", ") : key === "probability" && typeof val === "number" ? `${Math.round(val * 100)}%` : textValue(val)}`,
    );
  return (
    <span>
      {fact.status === "disputed" && (
        <strong className="disputed-text">Спорное значение · </strong>
      )}
      {v.available === false
        ? "Нет"
        : v.available === null
          ? "Не уточнено"
          : parts.length
            ? ""
            : typeof fact.value === "boolean"
              ? textValue(fact.value)
              : "Есть данные"}
      {parts.length > 0 && (
        <span>
          {v.available === false ? " · " : ""}
          {parts.join(" · ")}
        </span>
      )}
    </span>
  );
}
export function EvidenceButton({
  fact,
  claims = [],
}: {
  fact: Fact;
  claims?: Claim[];
}) {
  const [open, setOpen] = useState(false);
  const labels = {
    computed: fact.evidence.some((e) => e.kind === "quote")
      ? "Сайт отеля · цитата"
      : fact.evidence.some((e) => e.kind === "open_data")
        ? "Открытые данные"
        : "Рассчитано",
    confirmed: "Подтверждено отелем",
    disputed: "Есть противоречие",
    unknown: "Нет данных",
    stale: "Нужна перепроверка",
  };
  return (
    <>
      <button
        className={`evidence-button ${fact.status}`}
        onClick={() => setOpen(true)}
        data-testid={`evidence-${fact.characteristic_id}`}
      >
        <span className="tiny-dot" />
        {labels[fact.status]}
        <ArrowRight size={12} />
      </button>
      {open && (
        <EvidenceModal
          fact={fact}
          claims={claims}
          onClose={() => setOpen(false)}
        />
      )}
    </>
  );
}
export function EvidenceModal({
  fact: initialFact,
  claims: initialClaims = [],
  onClose,
}: {
  fact: Fact;
  claims?: Claim[];
  onClose: () => void;
}) {
  const expectedIds = [
    ...initialFact.supporting_claim_ids,
    ...(initialFact.conflicting_claim_ids || []),
  ];
  const deferred = expectedIds.some(
    (id) => !initialClaims.some((claim) => claim.id === id),
  );
  const full = useResource(
    () =>
      deferred
        ? api.factEvidence(initialFact.hotel_id, initialFact.id)
        : Promise.resolve(null),
    [initialFact.hotel_id, initialFact.id, initialFact.updated_at, deferred],
  );
  const fact = full.data?.fact || initialFact;
  const claims = full.data?.claims || initialClaims;
  const supporting = claims.filter((c) =>
    fact.supporting_claim_ids.includes(c.id),
  );
  const observed = supporting
    .map((c) => c.observed_at)
    .filter(Boolean)
    .sort()
    .at(-1);
  return (
    <Modal
      title={names[fact.characteristic_id] || fact.characteristic_id}
      onClose={onClose}
    >
      <div className="evidence-summary">
        <FactValue fact={fact} />
        <span className="muted">{scopeText(fact.scope)}</span>
      </div>
      {fact.synthetic && <Synthetic />}
      {deferred && full.loading && (
        <p className="muted" role="status">Загружаем полное доказательство и даты источников…</p>
      )}
      <ErrorState error={full.error} retry={full.reload} />
      <dl className="metadata">
        <div>
          <dt>Уверенность</dt>
          <dd>
            {Math.round(fact.confidence * 100)}%
            <small>Уверенность модели, не гарантия результата</small>
          </dd>
        </div>
        <div>
          <dt>Данные получены</dt>
          <dd>
            {observed ? dateText(observed) : "Дата наблюдения не передана"}
            <small>Когда источник наблюдал или рассчитал значение</small>
          </dd>
        </div>
        <div>
          <dt>Актуально до</dt>
          <dd>{dateText(fact.valid_until)}</dd>
        </div>
        <div>
          <dt>Метод</dt>
          <dd>
            {[...new Set(supporting.map((c) => c.method))].map((m) => (
              <span className="named-id" key={m}>
                {METHOD_NAMES[m] || m}
                {METHOD_NAMES[m] && <code>{m}</code>}
              </span>
            ))}
            {!supporting.length && "Метод не передан API"}
          </dd>
        </div>
        <div>
          <dt>Источник</dt>
          <dd>
            {[
              ...new Set(
                supporting.length
                  ? supporting.map((c) => c.source)
                  : fact.evidence.flatMap((e) => e.source_ids),
              ),
            ].map((src) => (
              <span className="named-id" key={src}>
                {SOURCE_NAMES[src] || src}
                {SOURCE_NAMES[src] && <code>{src}</code>}
              </span>
            ))}
            {!supporting.length &&
              !fact.evidence.some((e) => e.source_ids.length) &&
              "Нет подтверждающего источника"}
          </dd>
        </div>
      </dl>
      {fact.evidence.map((e) => (
        <article className="evidence-card" key={e.id}>
          <span className="eyebrow">
            {
              {
                calculation: "Расчёт",
                open_data: "Открытые данные",
                quote: "Дословная цитата",
                hotelier: "Ответ отеля",
                simulation: "Симуляция",
                mock: "Макет",
              }[e.kind]
            }
          </span>
          <h3>{e.label}</h3>
          {e.quote && <blockquote>«{e.quote}»</blockquote>}
          {(e.url || e.artifact) && (
            <a
              href={e.url || e.artifact!}
              target="_blank"
              rel="noreferrer"
              className="text-button"
            >
              Открыть источник <ExternalLink size={14} />
            </a>
          )}
          {e.limitations.map((l) => (
            <p className="muted" key={l}>
              {l}
            </p>
          ))}
          <details>
            <summary>Входы и результат расчёта</summary>
            <pre>
              {JSON.stringify({ inputs: e.inputs, output: e.output }, null, 2)}
            </pre>
          </details>
        </article>
      ))}
      {!fact.evidence.length && !deferred && (
        <p className="muted">
          Доказательств пока нет. Это неизвестное значение, оно не означает
          «нет».
        </p>
      )}
    </Modal>
  );
}
export function CompareButton({ hotelId }: { hotelId: string }) {
  const { compare, toggleCompare } = useApp();
  return (
    <button
      className={`compare-button ${compare.includes(hotelId) ? "active" : ""}`}
      onClick={() => toggleCompare(hotelId)}
      aria-pressed={compare.includes(hotelId)}
      data-testid={`compare-${hotelId}`}
    >
      {compare.includes(hotelId) ? <Check size={15} /> : <span>+</span>} В
      сравнение
    </button>
  );
}
export function BackLink({
  to = "/search",
  children = "К результатам",
}: {
  to?: string;
  children?: ReactNode;
}) {
  return (
    <Link to={to} className="back-link">
      ← {children}
    </Link>
  );
}
