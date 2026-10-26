import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { ArrowRight, Bell, Check, ClipboardCheck, Send } from "lucide-react";
import { api, dateText, textValue, useResource } from "../api";
import { useApp } from "../state";
import {
  BackLink,
  Empty,
  ErrorState,
  Loading,
  PageIntro,
  Synthetic,
} from "../components/ui";
import type { Passport, PassportItem } from "../types";
const statuses: Record<string, string> = {
  draft: "Черновик",
  sent: "Ожидает ответа отеля",
  confirmed: "Отель подтвердил",
  alternative: "Предложена альтернатива",
  at_risk: "Ожидание под угрозой",
  pending: "Ожидает подтверждения",
  declined: "Не подтверждено",
};
export function PassportView({
  passport,
  onUpdate,
  hotelier = false,
}: {
  passport: Passport;
  onUpdate: (p: Passport) => void;
  hotelier?: boolean;
}) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState<Error | null>(null),
    [room, setRoom] = useState(passport.room_number || ""),
    [alternative, setAlternative] = useState("");
  const act = async (
    action: "confirm" | "alternative" | "feedback",
    matched?: boolean,
  ) => {
    setBusy(true);
    setError(null);
    try {
      onUpdate(
        await api.passportAction(passport.id, {
          action,
          room_number: room,
          text: alternative,
          matched,
        }),
      );
    } catch (e) {
      setError(e as Error);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="passport-view">
      <div className="passport-status">
        <ClipboardCheck size={25} />
        <div>
          <span className="eyebrow">
            ПАСПОРТ {passport.id.slice(0, 8).toUpperCase()}
          </span>
          <h3>{statuses[passport.status] || passport.status}</h3>
        </div>
        {passport.synthetic_booking && <Synthetic>Бронь · синтетика</Synthetic>}
      </div>
      <div className="passport-meta">
        <span>{passport.guest_name}</span>
        <span>
          {dateText(passport.check_in)} — {dateText(passport.check_out)}
        </span>
        {passport.room_number && <strong>Номер {passport.room_number}</strong>}
      </div>
      {passport.items.map((item) => (
        <div className="passport-item" key={item.id}>
          <span
            className={`verdict-symbol ${item.status === "confirmed" ? "yes" : item.status === "at_risk" ? "no" : "unknown"}`}
          >
            {item.status === "confirmed"
              ? "✓"
              : item.status === "at_risk"
                ? "!"
                : "?"}
          </span>
          <div>
            <strong>{item.text}</strong>
            <p>
              {statuses[item.status] || item.status}
              {item.critical && " · критично"}
            </p>
            {item.response && <blockquote>{item.response}</blockquote>}
          </div>
        </div>
      ))}
      {passport.notifications.length > 0 && (
        <div className="notifications">
          <h4>
            <Bell size={17} />
            Что изменилось
          </h4>
          {passport.notifications.map((n, i) => (
            <p key={i}>{textValue(n.text || n.message || n.title || n)}</p>
          ))}
        </div>
      )}
      <div className="passport-timeline">
        <span className="eyebrow">ИСТОРИЯ ОЖИДАНИЙ</span>
        {passport.timeline.map((event, i) => (
          <div key={i}>
            <span className="timeline-dot" />
            <div>
              <p>
                {textValue(
                  event.text || event.message || event.action || event,
                )}
              </p>
              <small>
                {typeof event.at === "string" ? dateText(event.at) : ""}
              </small>
            </div>
          </div>
        ))}
      </div>
      {hotelier ? (
        <div className="passport-actions">
          <label>
            Номер
            <input
              value={room}
              onChange={(e) => setRoom(e.target.value)}
              placeholder="Подтверждённый номер"
            />
          </label>
          <label>
            Комментарий или альтернатива
            <textarea
              value={alternative}
              onChange={(e) => setAlternative(e.target.value)}
              placeholder="Уточните сторону, этаж и ограничения"
            />
          </label>
          <div className="button-row">
            <button
              className="button primary small"
              onClick={() => act("confirm")}
              disabled={busy || !room}
            >
              <Check size={15} />
              Подтвердить
            </button>
            <button
              className="button secondary small"
              onClick={() => act("alternative")}
              disabled={busy || !alternative}
            >
              Предложить альтернативу
            </button>
          </div>
        </div>
      ) : (
        <details className="feedback">
          <summary>После поездки: совпало ли с ожиданиями?</summary>
          <p>Демонстрационная обратная связь обновит историю паспорта.</p>
          <div className="button-row">
            <button
              className="button secondary small"
              disabled={busy}
              onClick={() => act("feedback", true)}
            >
              Да, совпало
            </button>
            <button
              className="button secondary small"
              disabled={busy}
              onClick={() => act("feedback", false)}
            >
              Были расхождения
            </button>
          </div>
        </details>
      )}
      <ErrorState error={error} />
    </div>
  );
}
export function BookingPage() {
  const { id = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const { parsed, searchResult } = useApp();
  const detail = useResource(() => api.hotel(id, parsed), [id, parsed]);
  const existing = useResource(
    () =>
      params.get("passport")
        ? api.passport(params.get("passport")!)
        : Promise.resolve(null),
    [params.get("passport")],
  );
  const [passport, setPassport] = useState<Passport | null>(null),
    [guest, setGuest] = useState("Маша"),
    [items, setItems] = useState<PassportItem[]>([]),
    [busy, setBusy] = useState(false),
    [error, setError] = useState<Error | null>(null);
  useEffect(() => {
    if (existing.data) setPassport(existing.data);
  }, [existing.data]);
  useEffect(() => {
    if (detail.data) {
      const criteria = [...(parsed?.must || []), ...(parsed?.nice || [])];
      const hit = searchResult?.results.find((r) => r.hotel.id === id);
      setItems(
        criteria.length
          ? criteria.map((c, index) => ({
              id: crypto.randomUUID(),
              characteristic_id: c.characteristic_id,
              text: `${c.label}${typeof c.value === "number" ? ` ${c.op === "lte" ? "≤" : c.op === "gte" ? "≥" : "="} ${c.value}${c.characteristic_id === "beach_walk" ? " мин" : ""}` : c.value === false ? ": нет" : ""}`,
              critical: index < (parsed?.must.length || 0),
              scope: c.scope || { type: "hotel" },
              fact_ids:
                hit?.verdicts.find(
                  (v) => v.characteristic_id === c.characteristic_id,
                )?.fact_ids || [],
              status: "pending",
              response: null,
            }))
          : [
              {
                id: crypto.randomUUID(),
                characteristic_id: "room_orientation",
                text: "Уточнить сторону, этаж и тип номера",
                critical: true,
                scope: { type: "hotel" },
                fact_ids: [],
                status: "pending",
                response: null,
              },
            ],
      );
    }
  }, [detail.data]);
  const create = async () => {
    setBusy(true);
    setError(null);
    try {
      const p = await api.createPassport({
        hotel_id: id,
        guest_name: guest,
        check_in: parsed?.check_in,
        check_out: parsed?.check_out,
        items,
        parsed,
      });
      setPassport(p);
      setParams({ passport: p.id });
    } catch (e) {
      setError(e as Error);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="page-container booking-page">
      <BackLink to={`/hotel/${id}`} children="К отелю" />
      <PageIntro
        eyebrow="ЧТОБЫ ВАЖНОЕ НЕ ПОТЕРЯЛОСЬ"
        title="Ваш паспорт ожиданий."
      >
        Не просто пожелание в комментарии. Структурированный запрос, на который
        отель даст конкретный ответ.
      </PageIntro>
      <div className="booking-layout">
        <div>
          {detail.loading && <Loading />}
          <ErrorState error={detail.error} retry={detail.reload} />
          {detail.data && (
            <div className="booking-hotel-label">
              <MapHotelMark />
              <div>
                <span className="eyebrow">ВАШ ОТЕЛЬ</span>
                <h3>{detail.data.hotel.name}</h3>
              </div>
              <Synthetic>Без оплаты · демобронь</Synthetic>
            </div>
          )}
          {passport ? (
            <PassportView passport={passport} onUpdate={setPassport} />
          ) : (
            <>
              {params.get("passport") && existing.loading && <Loading />}
              <ErrorState error={existing.error} retry={existing.reload} />
              {detail.data && (
                <section className="passport-editor">
                  <label className="guest-input">
                    Как к вам обращаться
                    <input
                      value={guest}
                      onChange={(e) => setGuest(e.target.value)}
                      maxLength={100}
                      data-testid="passport-guest"
                    />
                  </label>
                  <div className="section-heading">
                    <h2>На чём договоримся</h2>
                    <span className="fine-print">Отметьте критичное</span>
                  </div>
                  {items.map((item, index) => (
                    <div className="passport-edit-item" key={item.id}>
                      <span>0{index + 1}</span>
                      <input
                        aria-label={`Ожидание ${index + 1}`}
                        value={item.text}
                        onChange={(e) =>
                          setItems((old) =>
                            old.map((i) =>
                              i.id === item.id
                                ? { ...i, text: e.target.value }
                                : i,
                            ),
                          )
                        }
                      />
                      <label className="checkbox-label">
                        <input
                          type="checkbox"
                          checked={item.critical}
                          onChange={(e) =>
                            setItems((old) =>
                              old.map((i) =>
                                i.id === item.id
                                  ? { ...i, critical: e.target.checked }
                                  : i,
                              ),
                            )
                          }
                        />
                        Критично
                      </label>
                    </div>
                  ))}
                  {!items.length && (
                    <Empty title="Добавьте ожидания в поиске" />
                  )}
                  <p className="fine-print">
                    Все ожидания относятся к одному номеру. Если стороны или
                    этажи несовместимы, отель должен предложить альтернативу.
                  </p>
                  <button
                    className="button primary"
                    onClick={create}
                    disabled={
                      busy ||
                      !guest.trim() ||
                      !items.length ||
                      items.some((i) => !i.text.trim())
                    }
                    data-testid="passport-submit"
                  >
                    {busy ? "Отправляем…" : "Отправить отелю"}
                    <Send size={17} />
                  </button>
                  <ErrorState error={error} />
                </section>
              )}
            </>
          )}
        </div>
        <aside className="passport-explainer">
          <span className="eyebrow">ТРИ ШАГА К СПОКОЙСТВИЮ</span>
          {[
            [
              "01",
              "Запишите важное",
              "Сторона, этаж, тишина, особые условия. Критичное выделите отдельно.",
            ],
            [
              "02",
              "Получите ответ",
              "Отель подтвердит конкретный номер или предложит альтернативу.",
            ],
            [
              "03",
              "Оставайтесь в курсе",
              "Изменился вид или появились новые ограничения — это отразится в паспорте.",
            ],
          ].map(([n, title, text]) => (
            <div key={n}>
              <span>{n}</span>
              <h3>{title}</h3>
              <p>{text}</p>
            </div>
          ))}
          <Link to="/hotelier" className="text-button">
            Открыть кабинет отеля <ArrowRight size={15} />
          </Link>
        </aside>
      </div>
    </div>
  );
}
function MapHotelMark() {
  return (
    <div className="hotel-mark">
      <span>О!</span>
    </div>
  );
}
