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
import {
  api,
  dateText,
  readHotelierSession,
  record,
  request,
  textValue,
  useResource,
} from "../api";
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
import type { ChangeEvent, Fact, Geo } from "../types";
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
const rate = (value: unknown) =>
  typeof value === "number" &&
  Number.isFinite(value) &&
  value >= 0 &&
  value <= 1
    ? value
    : null;
function metricStatus(status: unknown) {
  if (status === "not_measured" || !status)
    return "Измерение ещё не опубликовано.";
  if (
    [
      "gold_changed",
      "gold_changed_recompute_required",
      "corpus_changed_recompute_required",
    ].includes(String(status))
  )
    return "Набор меток изменился. Нужен пересчёт; прошлые цифры не показаны как текущие.";
  if (status === "catalogue_changed_recompute_required")
    return "Каталог характеристик изменился. Нужен пересчёт; прошлые цифры не показаны как текущие.";
  if (
    [
      "scorer_changed_recompute_required",
      "parser_changed_recompute_required",
    ].includes(String(status))
  )
    return "Код оценки изменился. Нужен пересчёт; прошлые цифры не показаны как текущие.";
  return "Актуальность измерения не подтверждена. Числа появятся после пересчёта.";
}
function WeakRankingMetrics({ value }: { value: unknown }) {
  const report = record(value);
  const measured =
    report.status === "measured_against_frozen_weak_labels" &&
    report.human_labeled === false;
  const splits = record(report.by_split);
  const bounds = (value: unknown) => {
    const interval = record(value),
      low = rate(interval.lower),
      high = rate(interval.upper);
    return low !== null && high !== null && low <= high
      ? `${number(low, 3)}–${number(high, 3)}`
      : "Не измерено";
  };
  return (
    <section
      className="quality-group"
      data-testid="ranking-weak"
      data-metric-status={
        typeof report.status === "string" ? report.status : "not_measured"
      }
    >
      <div className="section-heading">
        <div>
          <span className="eyebrow">
            РАНЖИРОВАНИЕ · ОТДЕЛЬНЫЙ ПРОВЕРОЧНЫЙ НАБОР
          </span>
          <h2>Согласие со слабыми метками</h2>
        </div>
      </div>
      <p className="fine-print">
        Метки выведены из фактов, не человеческая оценка. Источники меток и
        модель ранжирования связаны: это согласие с зафиксированными правилами,
        а не независимая релевантность отелей или вероятность удачной поездки.
      </p>
      {measured ? (
        <>
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th scope="col">Часть</th>
                  <th scope="col">nDCG@5</th>
                  <th scope="col">Запросы в расчёте</th>
                  <th scope="col">Без положительных меток</th>
                  <th scope="col">Размечено в top-5</th>
                  <th scope="col">Границы nDCG@5</th>
                </tr>
              </thead>
              <tbody>
                {[
                  ["dev", "Development"],
                  ["holdout", "Holdout"],
                ].map(([key, title]) => {
                  const split = record(splits[key]);
                  return (
                    <tr key={key} data-testid={`ranking-weak-${key}`}>
                      <th scope="row">{title}</th>
                      <td>{number(rate(split.ndcg_at_5), 3)}</td>
                      <td>
                        {number(split.queries_with_measured_ndcg)} из{" "}
                        {number(split.queries)}
                      </td>
                      <td>
                        {number(split.queries_without_positive_judgments)}
                      </td>
                      <td>{percent(rate(split.top5_judged_share))}</td>
                      <td>{bounds(split.ndcg_at_5_bounds)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="fine-print">
            Development — часть для разработки, holdout — отложенная часть.
            Основная nDCG учитывает только запросы с положительными метками и
            полностью размеченным top-5. Запросы без положительных меток
            исключены. Неразмеченные кандидаты не считаются отрицательными; доля
            разметки и границы показывают оставшуюся неопределённость. Эти
            границы не являются доверительным интервалом.
          </p>
          <p className="fine-print">
            В узком окне таблица прокручивается по горизонтали.
          </p>
          {report.input_materialization_matches_frozen === false && (
            <p className="fine-print">
              Текущие факты отличаются от сохранённых входов. Числа относятся к
              зафиксированному снимку, а не к текущим профилям.
            </p>
          )}
        </>
      ) : (
        <p
          className="fine-print"
          role="status"
          data-testid="ranking-weak-unavailable"
        >
          {report.status === "measured_against_frozen_weak_labels"
            ? "Происхождение меток не подтверждено как слабая разметка. Числа не опубликованы в этом блоке."
            : metricStatus(report.status)}
        </p>
      )}
      {typeof report.status === "string" &&
        report.status !== "not_measured" && (
          <a
            className="text-button"
            href="/data/eval/ranking_results.json"
            target="_blank"
            rel="noreferrer"
          >
            Полный отчёт и происхождение меток <ExternalLink size={14} />
          </a>
        )}
    </section>
  );
}
function ParserIntentMetrics({ value }: { value: unknown }) {
  const report = record(value);
  const measured = report.status === "measured_against_authored_intent_cases";
  const splits = record(report.splits),
    negative = record(report.negative);
  return (
    <section
      className="quality-group"
      data-testid="parser-intent"
      data-metric-status={
        typeof report.status === "string" ? report.status : "not_measured"
      }
    >
      <div className="section-heading">
        <div>
          <span className="eyebrow">ПОИСК · АВТОРСКИЕ ЯЗЫКОВЫЕ ПРИМЕРЫ</span>
          <h2>Разбор поискового запроса</h2>
        </div>
      </div>
      <p className="fine-print">
        Это авторские языковые примеры с синтетическими ожидаемыми намерениями,
        не произвольная речь пользователей и не проверка фактов отелей. Исходный
        holdout показан отдельно; он не заменяет новый независимый
        пользовательский тест.
      </p>
      {measured ? (
        <>
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th scope="col">Часть исходного набора</th>
                  <th scope="col">Запросы</th>
                  <th scope="col">Точное совпадение намерения</th>
                  <th scope="col">Точность критериев (precision)</th>
                  <th scope="col">Полнота критериев (recall)</th>
                </tr>
              </thead>
              <tbody>
                {[
                  ["gold", "Весь набор", record(report.gold)],
                  ["development", "Development", record(splits.development)],
                  ["holdout", "Исходный holdout", record(splits.holdout)],
                ].map(([key, title, value]) => {
                  const split = record(value);
                  return (
                    <tr key={String(key)} data-testid={`parser-intent-${key}`}>
                      <th scope="row">{String(title)}</th>
                      <td>{number(split.queries)}</td>
                      <td>
                        {percent(rate(split.exact_match_rate))}
                        {typeof split.exact_matches === "number" && (
                          <small>
                            {" "}
                            · {number(split.exact_matches)} из{" "}
                            {number(split.queries)}
                          </small>
                        )}
                      </td>
                      <td>{percent(rate(split.criterion_precision))}</td>
                      <td>{percent(rate(split.criterion_recall))}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="fine-print">
            Точное совпадение учитывает ожидаемые критерии и явно проверяемые
            поля запроса, например месяц, даты и область номера. Общий набор
            включает обе части; это не три независимые выборки.
          </p>
          <p className="fine-print">
            В узком окне таблица прокручивается по горизонтали.
          </p>
          <div className="metric-row" data-testid="parser-intent-negative">
            <div>
              <span>Нерелевантные контексты</span>
              <strong>{number(negative.queries)}</strong>
            </div>
            <div>
              <span>С ложными критериями</span>
              <strong>{number(negative.queries_with_false_criteria)}</strong>
            </div>
            <div>
              <span>Доля с ложными критериями</span>
              <strong>{percent(rate(negative.false_criteria_rate))}</strong>
            </div>
            <div>
              <span>Лишних критериев выдано</span>
              <strong>{number(negative.criteria_emitted)}</strong>
            </div>
          </div>
        </>
      ) : (
        <p
          className="fine-print"
          role="status"
          data-testid="parser-intent-unavailable"
        >
          {metricStatus(report.status)}
        </p>
      )}
      {typeof report.status === "string" &&
        report.status !== "not_measured" && (
          <a
            className="text-button"
            href="/data/eval/parser_results.json"
            target="_blank"
            rel="noreferrer"
          >
            Полный отчёт и хэши корпуса <ExternalLink size={14} />
          </a>
        )}
    </section>
  );
}
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
          {q.filter_comparison ? (
            <section className="quality-group" data-testid="filter-comparison">
              <span className="eyebrow">
                СРАВНЕНИЕ ФИЛЬТРОВ · СОХРАНЁННАЯ МОДЕЛЬ
              </span>
              <h2>
                {number(q.filter_comparison.search.outside_radius_supported)}{" "}
                кандидатов за пределами радиуса
              </h2>
              <p>
                Модель нашла совместимые море и закат у{" "}
                {number(q.filter_comparison.search.outside_radius_supported)}{" "}
                отелей дальше {number(q.filter_comparison.settings.radius_m)} м
                от картированной береговой линии. Из них{" "}
                {number(q.filter_comparison.search.outside_radius_partial)} —
                предварительные результаты. Это дополнительные модельные
                кандидаты для проверки, а не реальные окна, рост конверсии или
                измеренная точность.
              </p>
              <div className="metric-row">
                <div>
                  <span>
                    Простой фильтр ≤
                    {number(q.filter_comparison.settings.radius_m)} м
                  </span>
                  <strong>
                    {number(q.filter_comparison.baseline.within_radius)}
                  </strong>
                  <small>
                    из {number(q.filter_comparison.population.hotels)} отелей
                  </small>
                </div>
                <div>
                  <span>Море и закат из одного номера</span>
                  <strong>
                    {number(q.filter_comparison.search.compatible_supported)}
                  </strong>
                  <small>
                    {number(q.filter_comparison.search.partial_supported)}{" "}
                    предварительных
                  </small>
                </div>
                <div>
                  <span>Вне радиуса · модель нашла оба</span>
                  <strong>
                    {number(
                      q.filter_comparison.search.outside_radius_supported,
                    )}
                  </strong>
                  <small>не прошли бы простой фильтр близости</small>
                </div>
                <div>
                  <span>Этажность неизвестна</span>
                  <strong>
                    {number(q.filter_comparison.population.unknown_floor_count)}
                  </strong>
                  <small>не даёт уверенно отрицать верхние этажи</small>
                </div>
              </div>
              <p className="fine-print">
                Месяц:{" "}
                {new Intl.DateTimeFormat("ru", {
                  month: "long",
                  timeZone: "UTC",
                }).format(
                  new Date(
                    Date.UTC(2026, q.filter_comparison.settings.month - 1, 15),
                  ),
                )}
                . Поиск с{" "}
                {number(q.filter_comparison.settings.search_min_floor)}-го
                этажа, уверенность ≥
                {percent(q.filter_comparison.settings.confidence_floor)}; для
                отрицательного вывода ≥
                {percent(
                  q.filter_comparison.settings.negative_confidence_floor,
                )}
                . Расстояние до берега — по прямой, не пешим маршрутом.
                Учитываются только расчёты по открытым данным, без
                демонстрационных ответов отеля и сценария «новый дом».
              </p>
              <details>
                <summary>
                  Что модель знает внутри радиуса и где остаются пробелы
                </summary>
                <div className="table-scroll">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Внутри радиуса</th>
                        <th>Положительные</th>
                        <th>Из них неполные</th>
                        <th>Модельные отрицательные</th>
                        <th>Неизвестно</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(["sea_view", "sunset_view"] as const).map((cid) => {
                        const v =
                          q.filter_comparison!.baseline.views_within_radius[
                            cid
                          ];
                        return (
                          <tr key={cid}>
                            <th>{names[cid]}</th>
                            <td>{number(v.positive)}</td>
                            <td>{number(v.partial_positive)}</td>
                            <td>{number(v.model_negative)}</td>
                            <td>{number(v.unknown)}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
                <p className="fine-print">
                  Отрицательный результат требует известной этажности и
                  уверенных отрицательных значений во всех смоделированных
                  фасадах и этажах. Он не означает «нет ни одного реального
                  окна».
                </p>
                <div className="table-scroll">
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>На всей выборке</th>
                        <th>
                          Положительные расчёты только с{" "}
                          {number(q.filter_comparison.settings.k_floor)}-го
                          этажа
                        </th>
                        <th>При уверенном отрицании ниже</th>
                        <th>Положительные расчёты с одного фасада</th>
                        <th>При уверенном отрицании остальных</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(["sea_view", "sunset_view"] as const).map((cid) => {
                        const v = q.filter_comparison!.scoped[cid];
                        return (
                          <tr key={cid}>
                            <th>{names[cid]}</th>
                            <td>
                              {number(v.observed_positive_only_at_or_above_k)}
                            </td>
                            <td>{number(v.model_only_at_or_above_k)}</td>
                            <td>
                              {number(v.observed_positive_only_one_facade)}
                            </td>
                            <td>{number(v.model_only_one_facade)}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
                <p className="fine-print">
                  Левые числа описывают только найденные положительные расчёты;
                  там, где расчёта нет, значение остаётся неизвестным. Строгие
                  числа справа дополнительно требуют уверенного «нет» для
                  остальных сторон и этажей. Фасады модели не заменяют
                  планировку номеров.
                </p>
                {q.filter_comparison.limitations.map((limitation) => (
                  <p className="fine-print" key={limitation}>
                    {limitation}
                  </p>
                ))}
                <p className="fine-print">
                  Версия данных: {q.filter_comparison.seed_version}.
                  Сформировано: {dateText(q.filter_comparison.generated_at)}.
                </p>
                <code>{q.filter_comparison.settings.reproduce}</code>
                <p>
                  <a
                    className="text-button"
                    href="/data/eval/filter_baseline.json"
                    target="_blank"
                    rel="noreferrer"
                  >
                    Полный отчёт и SHA256 входов <ExternalLink size={14} />
                  </a>
                </p>
              </details>
            </section>
          ) : (
            <p className="fine-print">
              Сравнение фильтров для текущей версии данных ещё не опубликовано.
            </p>
          )}
          {q.provenance && (
            <section className="quality-group" data-testid="fact-provenance">
              <span className="eyebrow">
                ПРОИСХОЖДЕНИЕ ЗНАЧЕНИЙ В ТЕКУЩЕМ СТЕНДЕ
              </span>
              <h2>
                {percent(q.provenance.synthetic_share)} известных значений
                опираются на синтетику
              </h2>
              <div className="metric-row">
                <div>
                  <span>Без синтетических оснований</span>
                  <strong>{number(q.provenance.non_synthetic)}</strong>
                </div>
                <div>
                  <span>Синтетические · включая смешанные</span>
                  <strong>{number(q.provenance.synthetic)}</strong>
                </div>
                <div>
                  <span>Смешанные · уже входят в синтетические</span>
                  <strong>{number(q.provenance.mixed)}</strong>
                </div>
                <div>
                  <span>Неизвестные, пустые, устаревшие, спорные</span>
                  <strong>{number(q.provenance.unknown_stale_disputed)}</strong>
                </div>
              </div>
              <p className="fine-print">
                Единица счёта — отдельное значение характеристики для стороны,
                этажа или месяца, а не отель. Знаменатель доли:{" "}
                {number(q.provenance.known)} известных актуальных значений.
                Смешанные значения считаются синтетическими и не выдаются за
                реальное подтверждение. Значения без синтетики тоже бывают
                модельными и требуют проверки.
              </p>
            </section>
          )}
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
          <WeakRankingMetrics value={q.ranking_weak} />
          {q.parser_intent && <ParserIntentMetrics value={q.parser_intent} />}
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
            <div data-testid="independent-ndcg">
              <span>Независимая оценка поиска · nDCG@5</span>
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
const DEMO_CHANGE_HOTEL = "osm-way-53284165";
export function ChangesPage() {
  const changes = useResource(api.changes),
    { hotels, toast } = useApp();
  const [session, setSession] = useState(readHotelierSession),
    [hotelId, setHotelId] = useState(""),
    [busy, setBusy] = useState(""),
    [error, setError] = useState<Error | null>(null),
    [selected, setSelected] = useState<ChangeEvent | null>(null);
  useEffect(() => {
    if (hotelId || !hotels.data?.length) return;
    // The shipped synthetic «new building» scenario lives at this hotel.
    const demo = hotels.data.find((h) => h.id === DEMO_CHANGE_HOTEL);
    setHotelId((demo || hotels.data[0]).id);
  }, [hotels.data, hotelId]);
  useEffect(() => {
    if (!selected && changes.data?.length) setSelected(changes.data[0]);
  }, [changes.data, selected]);
  const canAdminister = session?.role === "demo_admin";
  useEffect(() => {
    const changed = () => {
      setSession(readHotelierSession());
      setSelected(null);
      changes.setData(null);
      changes.reload();
    };
    window.addEventListener("okno-auth-expired", changed);
    window.addEventListener("okno-auth-changed", changed);
    return () => {
      window.removeEventListener("okno-auth-expired", changed);
      window.removeEventListener("okno-auth-changed", changed);
    };
  }, [changes.reload, changes.setData]);
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
  const notificationCount =
    typeof current?.payload.notification_count === "number"
      ? current.payload.notification_count
      : current?.notifications.length || 0;
  const hotel = hotels.data?.find(
    (h) => h.id === current?.affected_hotel_ids[0],
  );
  const snapshots = useResource(async () => {
    if (!current || !hotel) return null;
    const read = async (path: string): Promise<Geo | null> => {
      try {
        const document = await request<Record<string, unknown>>(path);
        const saved = record(document.geo || document);
        return saved.hotel_id === hotel.id ? (saved as Geo) : null;
      } catch {
        return null;
      }
    };
    const artifact = current.payload.geo_artifact;
    const [before, after] = await Promise.all([
      read(`/data/geo/${encodeURIComponent(hotel.id)}.json`),
      typeof artifact === "string" && artifact.startsWith("/data/")
        ? read(artifact)
        : api.geo(hotel.id).catch(() => null),
    ]);
    return { before, after };
  }, [current?.id, hotel?.id]);
  const geoBefore = Object.keys(record(current?.payload.geo_before)).length
    ? (record(current?.payload.geo_before) as Geo)
    : snapshots.data?.before;
  const geoAfter = Object.keys(record(current?.payload.geo_after)).length
    ? (record(current?.payload.geo_after) as Geo)
    : snapshots.data?.after;
  const rawFixture = record(current?.payload.fixture);
  const fixture =
    rawFixture.type === "Feature"
      ? (rawFixture as unknown as GeoJSON.Feature)
      : null;

  return (
    <div className="page-container changes-page">
      <PageIntro
        eyebrow="МИР МЕНЯЕТСЯ. ОЖИДАНИЯ — ТОЖЕ."
        title="Ничего не устаревает молча."
        art="changes"
      >
        Повторная проверка по сроку и событиям. Если факт меняется, затронутый
        паспорт получает уведомление.
      </PageIntro>
      {!canAdminister && (
        <div className="limitations" data-testid="changes-auth-prompt">
          <p>
            Просмотр изменений открыт всем. Запуск пересчёта и синтетического
            события доступен только демо-администратору.
          </p>
          <Link className="text-button" to="/hotelier">
            Войти в кабинет администратора <ArrowRight size={14} />
          </Link>
          {session?.role === "hotelier" && (
            <p className="fine-print">
              Текущий кабинет управляет только своими отелями; роль
              администратора эта сессия не предоставляет.
            </p>
          )}
        </div>
      )}
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
            disabled={!!busy || !hotelId || !canAdminister}
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
        <button
          className="text-button"
          disabled={!!busy || !canAdminister}
          onClick={refresh}
        >
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
                  {geoBefore && geoAfter ? (
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
                        <span>
                          После изменения{" "}
                          <Synthetic>Новый дом · синтетика</Synthetic>
                        </span>
                        <TravelMap
                          hotels={[hotel]}
                          activeId={hotel.id}
                          geo={geoAfter}
                          extraGeometry={fixture}
                        />
                      </div>
                    </div>
                  ) : (
                    <>
                      <TravelMap
                        hotels={[hotel]}
                        activeId={hotel.id}
                        extraGeometry={fixture}
                      />
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
                <h3>Локальные уведомления: {number(notificationCount)}</h3>
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
                    {notificationCount > 0
                      ? "Уведомления созданы. Детали и ссылки на паспорта видны только в авторизованном кабинете с доступом к соответствующему отелю."
                      : "API не сообщил о затронутых паспортах."}
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
