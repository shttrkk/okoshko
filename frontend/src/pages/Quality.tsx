import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  ArrowRight,
  Check,
  ChevronLeft,
  ChevronRight,
  ExternalLink,
  FlaskConical,
  RefreshCw,
} from "lucide-react";
import { api, dateText, record, textValue, useResource } from "../api";
import { useApp } from "../state";
import { TravelMap } from "../components/Map";
import {
  Empty,
  ErrorState,
  FactValue,
  Loading,
  names,
  PageIntro,
  scopeText,
  Synthetic,
} from "../components/ui";
import type { ChangeEvent, Fact } from "../types";
const number = (value: unknown, digits = 0) =>
  typeof value === "number"
    ? new Intl.NumberFormat("ru", { maximumFractionDigits: digits }).format(
        value,
      )
    : "Не измерено";
const percent = (value: unknown) =>
  typeof value === "number"
    ? new Intl.NumberFormat("ru", {
        style: "percent",
        maximumFractionDigits: value > 0 && value < 0.01 ? 2 : 1,
      }).format(value)
    : "Не измерено";
export function QualityPage() {
  const quality = useResource(api.quality);
  const q = quality.data,
    coverage = record(q?.coverage),
    groups = record(q?.label_groups);
  return (
    <div className="page-container quality-page">
      <PageIntro
        eyebrow="ДОВЕРИЕ ДОЛЖНО БЫТЬ ПРОВЕРЯЕМЫМ"
        title="Что мы знаем о качестве."
        action={
          <Link className="button secondary" to="/quality/label">
            Разметить вручную <ArrowRight size={16} />
          </Link>
        }
      >
        Покрытие не равно точности. Точность возможна только там, где есть
        независимые метки.
      </PageIntro>
      {quality.loading && <Loading />}
      <ErrorState error={quality.error} retry={quality.reload} />
      {q && (
        <>
          <div className="quality-stats">
            <div>
              <span className="eyebrow">ПРОФИЛИ</span>
              <strong>{number(q.hotel_count)}</strong>
              <span>отелей в каталоге</span>
            </div>
            <div>
              <span className="eyebrow">ПОКРЫТИЕ ФАКТАМИ</span>
              <strong>{percent(coverage.ratio)}</strong>
              <span>
                {number(coverage.known_pairs)} из {number(coverage.total_pairs)}{" "}
                пар
              </span>
            </div>
            <div>
              <span className="eyebrow">РУЧНАЯ РАЗМЕТКА</span>
              <strong>{number(q.manual_label_count)}</strong>
              <span>независимых меток</span>
            </div>
            <div>
              <span className="eyebrow">ВОПРОСЫ ОТЕЛЬЕРУ</span>
              <strong>{percent(q.question_share)}</strong>
              <span>по определению метрики API</span>
            </div>
          </div>
          <section className="quality-evaluation">
            <div className="section-heading">
              <div>
                <span className="eyebrow">
                  РАЗНЫЕ ОСНОВАНИЯ — РАЗНЫЕ ВЫВОДЫ
                </span>
                <h2>С чем сравнивали</h2>
              </div>
              <span className="badge">
                {q.status === "awaiting_independent_labels"
                  ? "Ожидает независимой разметки"
                  : q.status === "measured"
                    ? "Есть размеченные примеры"
                    : textValue(q.status)}
              </span>
            </div>
            {Object.entries(groups).length ? (
              Object.entries(groups).map(([name, data]) => {
                const g = record(data);
                const characteristics = record(g.characteristics);
                return (
                  <article className="quality-group" key={name}>
                    <div className="section-heading">
                      <div>
                        <span className="eyebrow">
                          {name === "manual"
                            ? "НЕЗАВИСИМАЯ ПРОВЕРКА"
                            : name === "weak_website"
                              ? "СЛАБАЯ РАЗМЕТКА"
                              : "ПРОВЕРОЧНЫЙ НАБОР"}
                        </span>
                        <h3>
                          {name === "manual"
                            ? "Человек + проверяемый источник"
                            : name === "weak_website"
                              ? "Утверждения с сайтов отелей"
                              : name}
                        </h3>
                      </div>
                      <span>{number(g.label_count)} меток</span>
                    </div>
                    {name !== "manual" && (
                      <p className="fine-print">
                        {name === "weak_website"
                          ? "Самоописание отеля — слабая метка. Эти результаты нельзя выдавать за независимую точность."
                          : "Синтетический или внутренний набор показывает работу механизма и не измеряет точность на реальных отелях."}
                      </p>
                    )}
                    <div className="metric-row">
                      {[
                        ["accuracy", "Accuracy"],
                        ["precision", "Precision"],
                        ["recall", "Recall"],
                        ["brier", "Brier"],
                        ["ece", "ECE"],
                      ].map(([key, label]) => (
                        <div key={key}>
                          <span>{label}</span>
                          <strong>
                            {key === "brier" || key === "ece"
                              ? number(g[key], 3)
                              : percent(g[key])}
                          </strong>
                        </div>
                      ))}
                    </div>
                    {Object.keys(characteristics).length > 0 && (
                      <div className="table-scroll">
                        <table className="data-table">
                          <thead>
                            <tr>
                              <th>Характеристика</th>
                              <th>Метки</th>
                              <th>Оценено</th>
                              <th>Точность</th>
                              <th>Воздержания</th>
                            </tr>
                          </thead>
                          <tbody>
                            {Object.entries(characteristics).map(
                              ([id, metrics]) => {
                                const m = record(metrics);
                                return (
                                  <tr key={id}>
                                    <th>{names[id] || id}</th>
                                    <td>{number(m.label_count)}</td>
                                    <td>{number(m.evaluated)}</td>
                                    <td>{percent(m.accuracy)}</td>
                                    <td>{number(m.abstentions)}</td>
                                  </tr>
                                );
                              },
                            )}
                          </tbody>
                        </table>
                      </div>
                    )}
                    {Array.isArray(g.risk_coverage) &&
                      g.risk_coverage.length > 0 && (
                        <details>
                          <summary>Порог уверенности и покрытие</summary>
                          <div className="table-scroll">
                            <table className="data-table">
                              <thead>
                                <tr>
                                  <th>Порог</th>
                                  <th>Покрытие</th>
                                  <th>Точность</th>
                                </tr>
                              </thead>
                              <tbody>
                                {g.risk_coverage.map((r, i) => {
                                  const m = record(r);
                                  return (
                                    <tr key={i}>
                                      <td>{number(m.threshold, 2)}</td>
                                      <td>{percent(m.coverage)}</td>
                                      <td>{percent(m.accuracy)}</td>
                                    </tr>
                                  );
                                })}
                              </tbody>
                            </table>
                          </div>
                        </details>
                      )}
                  </article>
                );
              })
            ) : (
              <Empty title="Оценка ещё не опубликована">
                Сначала нужна разметка. Метрики появятся после сопоставления с
                независимыми наблюдениями.
              </Empty>
            )}
          </section>
          {q.synthetic_parser && (
            <section className="synthetic-parser quality-group">
              <Synthetic>Синтетический набор · проверка парсера</Synthetic>
              <h3>Разбор ответов на регрессионных примерах</h3>
              <div className="metric-row">
                <div>
                  <span>Примеры</span>
                  <strong>
                    {number(record(q.synthetic_parser).evaluated)}
                  </strong>
                </div>
                <div>
                  <span>Совпадение ожидаемых полей</span>
                  <strong>
                    {percent(record(q.synthetic_parser).accuracy)}
                  </strong>
                </div>
              </div>
              <p className="fine-print">
                {textValue(record(q.synthetic_parser).limitation)}
              </p>
              <p className="fine-print">
                Этот результат не измеряет точность характеристик реальных
                отелей и не объединяется с ручной разметкой.
              </p>
            </section>
          )}
          <div className="quality-other">
            <div>
              <span>Точность разбора ответов отельера</span>
              <strong>{percent(q.parser_accuracy)}</strong>
            </div>
            <div>
              <span>Поиск · nDCG@5</span>
              <strong>{number(q.ndcg_at_5, 3)}</strong>
            </div>
          </div>
          <div className="limitations">
            <h3>Как читать эти цифры</h3>
            {typeof q.methodology_url === "string" && (
              <a
                className="text-button"
                href={q.methodology_url}
                target="_blank"
                rel="noreferrer"
              >
                Методика и определения <ExternalLink size={14} />
              </a>
            )}
            {Array.isArray(q.limitations) ? (
              q.limitations.map((l, i) => <p key={i}>{textValue(l)}</p>)
            ) : (
              <p>
                API не передал описание ограничений. Неизмеренные метрики не
                заменяются вымышленными значениями.
              </p>
            )}
            <p>
              Сгенерировано:{" "}
              {typeof q.generated_at === "string"
                ? dateText(q.generated_at)
                : "дата не передана"}
            </p>
          </div>
        </>
      )}
    </div>
  );
}
export function LabelPage() {
  const targets = useResource(api.labels),
    { toast } = useApp();
  const [index, setIndex] = useState(0),
    [factId, setFactId] = useState(""),
    [value, setValue] = useState(""),
    [url, setUrl] = useState(""),
    [note, setNote] = useState(""),
    [labeler, setLabeler] = useState("manual"),
    [busy, setBusy] = useState(false),
    [error, setError] = useState<Error | null>(null);
  const target = targets.data?.[index],
    fact = target?.facts.find((f) => f.id === factId) || target?.facts[0];
  useEffect(() => {
    setFactId("");
    setValue("");
    setUrl("");
    setNote("");
  }, [index]);
  const save = async () => {
    if (!target || !fact) return;
    setBusy(true);
    setError(null);
    try {
      let parsed: unknown;
      try {
        parsed = JSON.parse(value);
      } catch {
        throw new Error(
          "Значение должно быть валидным JSON: true, false, null или объект согласно схеме характеристики.",
        );
      }
      await api.label({
        hotel_id: target.hotel.id,
        characteristic_id: fact.characteristic_id,
        scope: fact.scope,
        value: parsed,
        evidence_url: url,
        note,
        labeler,
      });
      toast("Независимая метка сохранена");
      setNote("");
      setValue("");
      targets.reload();
    } catch (e) {
      setError(e as Error);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="page-container label-page">
      <Link className="back-link" to="/quality">
        ← К качеству
      </Link>
      <PageIntro
        eyebrow="НЕЗАВИСИМАЯ ПРОВЕРКА"
        title="Посмотреть. Сопоставить. Отметить."
      >
        Оценивайте по внешнему доказательству. Если его нет — не подтверждайте
        расчёт по самому расчёту.
      </PageIntro>
      {targets.loading && <Loading />}
      <ErrorState error={targets.error} retry={targets.reload} />
      {target && (
        <>
          <div className="operations-toolbar">
            <div className="button-row">
              <button
                className="icon-button"
                aria-label="Предыдущий отель"
                disabled={index === 0}
                onClick={() => setIndex(index - 1)}
              >
                <ChevronLeft />
              </button>
              <span>
                {index + 1} / {targets.data?.length}
              </span>
              <button
                className="icon-button"
                aria-label="Следующий отель"
                disabled={index >= (targets.data?.length || 0) - 1}
                onClick={() => setIndex(index + 1)}
              >
                <ChevronRight />
              </button>
            </div>
            <Link to={`/hotel/${target.hotel.id}`}>
              {target.hotel.name} <ExternalLink size={14} />
            </Link>
          </div>
          <div className="label-layout">
            <section className="label-evidence">
              <TravelMap hotels={[target.hotel]} activeId={target.hotel.id} />
              <h3>Материалы для проверки</h3>
              {target.evidence_links.map((entry, i) => {
                const e = record(entry),
                  href =
                    typeof entry === "string"
                      ? entry
                      : String(e.url || e.artifact || "");
                return href ? (
                  <a
                    className="evidence-link"
                    key={i}
                    href={href}
                    target="_blank"
                    rel="noreferrer"
                  >
                    {String(e.label || e.title || href)}
                    <ExternalLink size={14} />
                  </a>
                ) : (
                  <p key={i}>{textValue(entry)}</p>
                );
              })}
              {!target.evidence_links.length && (
                <p className="muted">
                  API не передал внешние материалы. Укажите проверяемый источник
                  самостоятельно.
                </p>
              )}
              <p className="fine-print">
                Существующих меток: {target.existing_labels.length}. Фото, если
                доступны, находятся по ссылкам источников.
              </p>
            </section>
            <section className="label-form">
              <label>
                Характеристика
                <select
                  value={fact?.id || ""}
                  onChange={(e) => {
                    setFactId(e.target.value);
                    setValue("");
                  }}
                >
                  {target.facts.map((f) => (
                    <option key={f.id} value={f.id}>
                      {names[f.characteristic_id] || f.characteristic_id} ·{" "}
                      {scopeText(f.scope)}
                    </option>
                  ))}
                </select>
              </label>
              {fact && (
                <>
                  <div className="current-fact">
                    <span className="eyebrow">ПРОВЕРЯЕМЫЙ РАСЧЁТ</span>
                    <FactValue fact={fact} />
                    <p>{scopeText(fact.scope)}</p>
                  </div>
                  <label>
                    Независимое значение · JSON
                    <textarea
                      rows={4}
                      value={value}
                      onChange={(e) => setValue(e.target.value)}
                      placeholder={'{"available": true}'}
                      data-testid="label-value"
                    />
                  </label>
                  <div className="button-row">
                    {["true", "false", "null"].map((v) => (
                      <button
                        className="button secondary small"
                        key={v}
                        onClick={() => setValue(v)}
                      >
                        {v === "true"
                          ? "Да"
                          : v === "false"
                            ? "Нет"
                            : "Неизвестно"}
                      </button>
                    ))}
                  </div>
                  <p className="fine-print">
                    Для составной характеристики используйте объект по её схеме.
                  </p>
                  <label>
                    Ссылка на доказательство
                    <input
                      type="url"
                      value={url}
                      onChange={(e) => setUrl(e.target.value)}
                      placeholder="https://…"
                      data-testid="label-evidence-url"
                    />
                  </label>
                  <label>
                    Комментарий / дословная цитата
                    <textarea
                      value={note}
                      onChange={(e) => setNote(e.target.value)}
                      rows={3}
                    />
                  </label>
                  <label>
                    Автор разметки
                    <input
                      value={labeler}
                      onChange={(e) => setLabeler(e.target.value)}
                    />
                  </label>
                  <button
                    className="button primary"
                    disabled={busy || !value || !url || !labeler}
                    onClick={save}
                    data-testid="label-submit"
                  >
                    <Check size={16} />
                    Сохранить метку
                  </button>
                  <ErrorState error={error} />
                </>
              )}
            </section>
          </div>
        </>
      )}
      {targets.data && !targets.data.length && (
        <Empty title="Нет объектов для разметки" />
      )}
    </div>
  );
}
function FactSnapshot({ facts }: { facts: Fact[] }) {
  return (
    <div className="change-facts">
      {facts.map((f) => (
        <div key={f.id}>
          <strong>{names[f.characteristic_id] || f.characteristic_id}</strong>
          <span>{scopeText(f.scope)}</span>
          <FactValue fact={f} />
          <small>
            {f.status} · {Math.round(f.confidence * 100)}%
          </small>
        </div>
      ))}
    </div>
  );
}
export function ChangesPage() {
  const changes = useResource(api.changes),
    { hotels, toast } = useApp();
  const [hotelId, setHotelId] = useState(""),
    [busy, setBusy] = useState(""),
    [error, setError] = useState<Error | null>(null),
    [selected, setSelected] = useState<ChangeEvent | null>(null);
  useEffect(() => {
    if (!hotelId && hotels.data?.[0]) setHotelId(hotels.data[0].id);
  }, [hotels.data, hotelId]);
  const simulate = async () => {
    setBusy("simulate");
    setError(null);
    try {
      const event = await api.simulateChange(hotelId || undefined);
      setSelected(event);
      changes.reload();
    } catch (e) {
      setError(e as Error);
    } finally {
      setBusy("");
    }
  };
  const refresh = async () => {
    setBusy("refresh");
    setError(null);
    try {
      const r = await api.refresh();
      toast(`Перепроверено: ${r.processed}. Прогон ${r.run_id.slice(0, 8)}`);
      changes.reload();
    } catch (e) {
      setError(e as Error);
    } finally {
      setBusy("");
    }
  };
  const current = selected || changes.data?.[0];
  const hotel = hotels.data?.find(
    (h) => h.id === current?.affected_hotel_ids[0],
  );
  const geoBefore = record(current?.payload.geo_before),
    geoAfter = record(current?.payload.geo_after);
  return (
    <div className="page-container changes-page">
      <PageIntro
        eyebrow="МИР МЕНЯЕТСЯ. ОЖИДАНИЯ — ТОЖЕ."
        title="Ничего не устаревает молча."
      >
        Повторная проверка по сроку и событиям. Если факт меняется, затронутый
        паспорт получает уведомление.
      </PageIntro>
      <section className="change-simulator">
        <div>
          <Synthetic>Симуляция · синтетика</Synthetic>
          <h2>А если рядом появился дом?</h2>
          <p>
            Демонстрационное событие пересчитает видимость и покажет последствия
            для ожиданий гостей.
          </p>
        </div>
        <div>
          <label className="sr-only" htmlFor="change-hotel">
            Отель для симуляции
          </label>
          <select
            id="change-hotel"
            value={hotelId}
            onChange={(e) => setHotelId(e.target.value)}
          >
            {hotels.data?.map((h) => (
              <option key={h.id} value={h.id}>
                {h.name}
              </option>
            ))}
          </select>
          <button
            className="button primary"
            disabled={!!busy || !hotelId}
            onClick={simulate}
            data-testid="simulate-change"
          >
            <FlaskConical size={16} />
            Добавить 16-этажный дом
          </button>
        </div>
      </section>
      <div className="operations-toolbar">
        <span className="eyebrow">ЛЕНТА АКТУАЛИЗАЦИИ</span>
        <button className="text-button" disabled={!!busy} onClick={refresh}>
          <RefreshCw size={14} />
          Перепроверить истёкшие TTL
        </button>
      </div>
      <ErrorState error={error} />
      <ErrorState error={changes.error} retry={changes.reload} />
      {changes.loading && <Loading />}
      <div className="changes-layout">
        <aside className="change-list">
          {changes.data?.map((c) => (
            <button
              className={current?.id === c.id ? "selected" : ""}
              key={c.id}
              onClick={() => setSelected(c)}
            >
              <span className="eyebrow">{dateText(c.detected_at)}</span>
              <strong>{c.title}</strong>
              {c.synthetic && <Synthetic />}
              <small>{c.affected_hotel_ids.length} отелей</small>
            </button>
          ))}
        </aside>
        <section className="change-detail">
          {current ? (
            <>
              <div className="section-heading">
                <div>
                  <span className="eyebrow">{current.kind}</span>
                  <h2>{current.title}</h2>
                </div>
                {current.synthetic && <Synthetic />}
              </div>
              {hotel && (
                <>
                  <p className="muted">{hotel.name}</p>
                  {Object.keys(geoBefore).length &&
                  Object.keys(geoAfter).length ? (
                    <div className="change-map-pair">
                      <div>
                        <span>До изменения</span>
                        <TravelMap
                          hotels={[hotel]}
                          activeId={hotel.id}
                          geo={geoBefore}
                        />
                      </div>
                      <div>
                        <span>После изменения</span>
                        <TravelMap
                          hotels={[hotel]}
                          activeId={hotel.id}
                          geo={geoAfter}
                        />
                      </div>
                    </div>
                  ) : (
                    <>
                      <TravelMap hotels={[hotel]} activeId={hotel.id} />
                      <p className="fine-print">
                        Карта показывает актуальные данные. Исторические геослои
                        API не передал — изменение сравнивается по сохранённым
                        фактам ниже.
                      </p>
                    </>
                  )}
                </>
              )}
              <div className="before-after">
                <section>
                  <span className="eyebrow">БЫЛО</span>
                  <FactSnapshot facts={current.before} />
                  {!current.before.length && (
                    <p className="muted">Предыдущие факты не переданы</p>
                  )}
                </section>
                <section>
                  <span className="eyebrow">СТАЛО</span>
                  <FactSnapshot facts={current.after} />
                  {!current.after.length && (
                    <p className="muted">Новые факты не переданы</p>
                  )}
                </section>
              </div>
              <div className="notifications">
                <h3>Кого уведомили</h3>
                {current.notifications.length ? (
                  current.notifications.map((n, i) => (
                    <div key={i}>
                      <p>{textValue(n.text || n.message || n.title || n)}</p>
                      {typeof n.passport_id === "string" && hotel && (
                        <Link
                          to={`/booking/${hotel.id}?passport=${n.passport_id}`}
                        >
                          Паспорт ожиданий <ArrowRight size={13} />
                        </Link>
                      )}
                    </div>
                  ))
                ) : (
                  <p className="muted">
                    API не сообщил о затронутых паспортах.
                  </p>
                )}
              </div>
              <details>
                <summary>Проверяемые входы события</summary>
                <pre>{JSON.stringify(current.payload, null, 2)}</pre>
              </details>
            </>
          ) : (
            <Empty title="Пока нет событий">
              Запустите перепроверку или синтетический сценарий изменения.
            </Empty>
          )}
        </section>
      </div>
    </div>
  );
}
