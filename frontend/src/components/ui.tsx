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
import { api, ApiError, dateText, record, textValue } from "../api";
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
        `${scope.floor_min}${scope.floor_max && scope.floor_max !== scope.floor_min ? `–${scope.floor_max}` : "+"} этаж`,
      scope.room_type,
      scope.month && `${scope.month}-й месяц`,
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
    ref.current?.focus();
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeRef.current();
      if (e.key === "Tab") {
        const els = ref.current?.querySelectorAll<HTMLElement>(
          'button,a,input,select,textarea,[tabindex="0"]',
        );
        if (!els?.length) return;
        const first = els[0],
          last = els[els.length - 1];
        if (e.shiftKey && document.activeElement === first) {
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
  compact = false,
}: {
  hotelId: string;
  characteristicId: string;
  compact?: boolean;
}) {
  const [busy, setBusy] = useState(false),
    [sent, setSent] = useState(false),
    [error, setError] = useState<Error | null>(null);
  const { toast } = useApp();
  const ask = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.ask(hotelId, characteristicId);
      setSent(true);
      toast(
        "Вопрос добавлен в кабинет отельера. Ответ дополнит профиль отеля.",
      );
    } catch (e) {
      setError(e as Error);
    } finally {
      setBusy(false);
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
        {sent ? "Вопрос отправлен" : compact ? "Спросить" : "Спросить отель"}
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
}: {
  verdicts: Verdict[];
  hotelId: string;
  onEvidence?: (v: Verdict) => void;
}) {
  return (
    <ul className="verdict-list">
      {verdicts.map((v, i) => (
        <li key={`${v.characteristic_id}-${i}`}>
          <span
            className={`verdict-symbol ${v.status}`}
            aria-label={
              {
                yes: "Подходит",
                partial: "Частично",
                no: "Не подходит",
                unknown: "Неизвестно",
              }[v.status]
            }
          >
            {v.symbol}
          </span>
          <div>
            <button
              className="verdict-text"
              onClick={() => onEvidence?.(v)}
              disabled={!onEvidence}
            >
              <strong>{v.label}</strong>
              {v.synthetic && <Synthetic>Подтверждение · синтетика</Synthetic>}
              <span>{v.text}</span>
              {v.caveat && <small>{v.caveat}</small>}
            </button>
            {v.status === "unknown" && (
              <AskButton
                compact
                hotelId={hotelId}
                characteristicId={v.characteristic_id}
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
}: {
  hotel: Hotel;
  className?: string;
}) {
  const local = hotel.image?.startsWith("/data/");
  return (
    <div className={`hotel-visual ${className}`}>
      {local ? (
        <img src={hotel.image!} alt={hotel.name} loading="lazy" />
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
export function FactValue({ fact }: { fact: Fact }) {
  const v = record(fact.value);
  if (fact.status === "unknown" || fact.value === null)
    return <span className="unknown-text">? Нет данных</span>;
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
  fact,
  claims = [],
  onClose,
}: {
  fact: Fact;
  claims?: Claim[];
  onClose: () => void;
}) {
  const supporting = claims.filter((c) =>
    fact.supporting_claim_ids.includes(c.id),
  );
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
      <dl className="metadata">
        <div>
          <dt>Уверенность</dt>
          <dd>
            {Math.round(fact.confidence * 100)}%
            <small>Уверенность модели, не гарантия результата</small>
          </dd>
        </div>
        <div>
          <dt>Обновлено</dt>
          <dd>{dateText(fact.updated_at)}</dd>
        </div>
        <div>
          <dt>Следующая проверка / TTL</dt>
          <dd>{dateText(fact.valid_until)}</dd>
        </div>
        <div>
          <dt>Метод</dt>
          <dd>
            {supporting.map((c) => c.method).join(", ") ||
              "Метод не передан API"}
          </dd>
        </div>
        <div>
          <dt>Источник</dt>
          <dd>
            {supporting.map((c) => c.source).join(", ") ||
              fact.evidence.flatMap((e) => e.source_ids).join(", ") ||
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
      {!fact.evidence.length && (
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
