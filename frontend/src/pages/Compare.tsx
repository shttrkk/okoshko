import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ArrowRight, X } from "lucide-react";
import { api, useResource } from "../api";
import { TzFactDetails } from "../components/TzFactDetails";
import { isSupplementalFact } from "../components/tz-facts";
import "../tz-facts.css";
import {
  reliability,
  factDemoLabel,
  humanExplanation,
  verdictLabel,
  verdictSymbol,
  verdictTone,
} from "../components/panel-guest-presentation";
import { useApp } from "../state";
import {
  AskButton,
  Empty,
  ErrorState,
  EvidenceButton,
  HotelVisual,
  Loading,
  names,
  needsQuestion,
  PageIntro,
  Synthetic,
} from "../components/ui";
import {
  headline,
  isKnownFact,
  representative,
} from "../components/FactsPanel";
export function ComparePage() {
  const { compare, toggleCompare, parsed, hotels } = useApp();
  const [params, setParams] = useSearchParams(),
    [onlyDiff, setOnlyDiff] = useState(true);
  const ids =
    params.get("ids")?.split(",").filter(Boolean).slice(0, 4) || compare;
  const changeSelection = (hotelId: string) => {
    if (params.has("ids")) {
      const next = ids.includes(hotelId)
        ? ids.filter((i) => i !== hotelId)
        : [...ids, hotelId].slice(0, 4);
      setParams({ ids: next.join(",") });
    } else toggleCompare(hotelId);
  };
  const result = useResource(
    () =>
      ids.length >= 2
        ? api.compare(ids, parsed)
        : Promise.resolve({ hotels: [], characteristics: [] }),
    [ids, parsed],
  );
  const d = result.data;
  const priority = [...(parsed?.must || []), ...(parsed?.nice || [])].map(
    (c) => c.characteristic_id,
  );
  for (const room of parsed?.rooms || [])
    priority.push(
      ...[...room.must, ...room.nice].map(
        (criterion) => criterion.characteristic_id,
      ),
    );
  const allIds = [
    ...new Set(
      d?.hotels.flatMap((h) => h.facts.map((f) => f.characteristic_id)) || [],
    ),
  ].sort((a, b) => {
    const x = priority.indexOf(a),
      y = priority.indexOf(b);
    return (x < 0 ? 999 : x) - (y < 0 ? 999 : y);
  });
  const month = parsed?.month || 9;
  const summaryKey = (
    h: NonNullable<typeof d>["hotels"][number],
    id: string,
  ) => {
    const facts = h.facts.filter((f) => f.characteristic_id === id);
    const text = headline(id, facts, month);
    return typeof text === "string"
      ? text
      : JSON.stringify(representative(facts)?.value ?? null);
  };
  const rows = allIds.filter(
    (id) =>
      !onlyDiff || new Set(d?.hotels.map((h) => summaryKey(h, id))).size > 1,
  );
  return (
    <div className="page-container compare-page">
      <PageIntro
        eyebrow="РЯДОМ ВИДНО ЛУЧШЕ"
        title="Разные отели. Ваши приоритеты."
        art="compare"
      >
        Сначала обязательное, затем желательное. Неизвестное остаётся
        неизвестным.
      </PageIntro>
      <div className="compare-toolbar">
        <label className="switch-label">
          <input
            type="checkbox"
            checked={onlyDiff}
            onChange={(e) => setOnlyDiff(e.target.checked)}
          />
          <span />
          Только различия
        </label>
        <span>Выбрано {ids.length} · можно до 4 отелей</span>
      </div>
      {ids.length < 2 ? (
        <Empty
          title="Выберите хотя бы два отеля"
          action={
            <Link className="button primary" to="/search">
              К поиску <ArrowRight size={16} />
            </Link>
          }
        >
          Добавляйте отели в сравнение из выдачи или карточки.
        </Empty>
      ) : (
        <>
          {result.loading && <Loading />}
          <ErrorState error={result.error} retry={result.reload} />
          {d && d.hotels.length > 0 && (
            <div className="comparison-scroll">
              <table className="comparison-table">
                <thead>
                  <tr>
                    <th>
                      <span className="eyebrow">ВАШИ ОРИЕНТИРЫ</span>
                      <p>
                        {parsed?.text ||
                          "Сопоставление по всем доступным характеристикам"}
                      </p>
                    </th>
                    {d.hotels.map((h) => (
                      <th key={h.hotel.id}>
                        <HotelVisual
                          hotel={h.hotel}
                          className="compare-visual"
                        />
                        <div className="compare-hotel-head">
                          <span className="eyebrow">{h.hotel.address}</span>
                          <Link to={`/hotel/${h.hotel.id}`}>
                            {h.hotel.name}
                            <ArrowRight size={15} />
                          </Link>
                          <button
                            className="icon-button"
                            aria-label={`Убрать ${h.hotel.name}`}
                            onClick={() => changeSelection(h.hotel.id)}
                          >
                            <X size={15} />
                          </button>
                        </div>
                        <p>{h.room_recommendation}</p>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((id) => (
                    <tr key={id}>
                      <th scope="row">
                        {d.characteristics.find((s) => s.id === id)
                          ?.display_name ||
                          d.characteristics.find((s) => s.id === id)?.name_ru ||
                          names[id] ||
                          id}
                        {parsed?.must.some(
                          (c) => c.characteristic_id === id,
                        ) && <span className="row-required">Обязательно</span>}
                      </th>
                      {d.hotels.map((h) => {
                        const allFacts = h.facts.filter(
                          (f) => f.characteristic_id === id,
                        );
                        const known = allFacts.filter(isKnownFact);
                        const verdict = h.verdicts.find(
                          (v) => v.characteristic_id === id,
                        );
                        const rep = allFacts.length
                          ? representative(allFacts)
                          : null;
                        return (
                          <td
                            className={
                              verdict?.status === "yes" && !verdict.synthetic
                                ? "compare-best"
                                : verdict?.status === "no"
                                  ? "compare-worst"
                                  : ""
                            }
                            key={h.hotel.id}
                          >
                            {verdict && (
                              <p className="compare-verdict">
                                <span
                                  className={`verdict-symbol ${verdictTone(verdict)}`}
                                >
                                  {verdictSymbol(verdict)}
                                </span>
                                <span
                                  className={`badge ${verdict.synthetic ? "synthetic" : "unknown-badge"}`}
                                >
                                  {verdictLabel(verdict)}
                                </span>
                                {humanExplanation(
                                  verdict.text.split(";")[0],
                                  verdict.synthetic,
                                )}
                              </p>
                            )}
                            {(known.length ||
                              allFacts.some(isSupplementalFact)) &&
                            rep ? (
                              <div className="compare-fact">
                                <p className="compare-summary">
                                  {headline(id, allFacts, month)}
                                </p>
                                {known.some((f) => f.synthetic) && (
                                  <Synthetic>
                                    {factDemoLabel(
                                      known.find((fact) => fact.synthetic)!,
                                    )}
                                  </Synthetic>
                                )}
                                {allFacts.some(isSupplementalFact) ? (
                                  <details className="verdict-caveats">
                                    <summary>Подробнее и основания</summary>
                                    <TzFactDetails
                                      facts={allFacts}
                                      claims={h.claims}
                                      month={month}
                                    />
                                  </details>
                                ) : (
                                  <details className="verdict-caveats">
                                    <summary>Надёжность данных</summary>
                                    <p>
                                      {reliability(
                                        Math.max(
                                          ...known.map((f) => f.confidence),
                                        ),
                                        known.some((f) => f.synthetic),
                                      )}{" "}
                                      · оценка полноты и согласованности
                                      источников
                                    </p>
                                  </details>
                                )}
                                {!allFacts.some(isSupplementalFact) && (
                                  <EvidenceButton
                                    fact={rep}
                                    claims={h.claims}
                                  />
                                )}
                                {needsQuestion(rep) &&
                                  d.characteristics.find(
                                    (spec) => spec.id === id,
                                  )?.hotelier_answerable === true && (
                                    <AskButton
                                      compact
                                      hotelId={h.hotel.id}
                                      characteristicId={id}
                                    />
                                  )}
                                {known.length > 1 && (
                                  <Link
                                    className="text-button"
                                    to={`/hotel/${h.hotel.id}`}
                                  >
                                    По сторонам и месяцам{" "}
                                    <ArrowRight size={12} />
                                  </Link>
                                )}
                              </div>
                            ) : (
                              <>
                                <span className="unknown-text">
                                  ? Нет данных
                                </span>
                                <AskButton
                                  compact
                                  hotelId={h.hotel.id}
                                  characteristicId={id}
                                />
                              </>
                            )}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
              {!rows.length && (
                <Empty title="По доступным данным различий нет">
                  Выключите «Только различия», чтобы посмотреть все значения.
                </Empty>
              )}
            </div>
          )}
        </>
      )}
      {hotels.data && ids.length < 4 && (
        <div className="add-compare">
          <label>
            Добавить отель
            <select value="" onChange={(e) => changeSelection(e.target.value)}>
              <option value="">Выберите из каталога</option>
              {hotels.data
                .filter((h) => !ids.includes(h.id))
                .map((h) => (
                  <option value={h.id} key={h.id}>
                    {h.name}
                  </option>
                ))}
            </select>
          </label>
        </div>
      )}
    </div>
  );
}
