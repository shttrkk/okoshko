import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ArrowRight, X } from "lucide-react";
import { api, useResource } from "../api";
import { useApp } from "../state";
import {
  AskButton,
  Empty,
  ErrorState,
  EvidenceButton,
  FactValue,
  Loading,
  names,
  PageIntro,
  scopeText,
} from "../components/ui";
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
  const allIds = [
    ...new Set(
      d?.hotels.flatMap((h) => h.facts.map((f) => f.characteristic_id)) || [],
    ),
  ].sort((a, b) => {
    const x = priority.indexOf(a),
      y = priority.indexOf(b);
    return (x < 0 ? 999 : x) - (y < 0 ? 999 : y);
  });
  const rows = allIds.filter(
    (id) =>
      !onlyDiff ||
      new Set(
        d?.hotels.map((h) =>
          JSON.stringify(
            h.facts
              .filter((f) => f.characteristic_id === id)
              .map((f) => ({
                value: f.value,
                scope: f.scope,
                status: f.status,
              })),
          ),
        ),
      ).size > 1,
  );
  return (
    <div className="page-container compare-page">
      <PageIntro
        eyebrow="РЯДОМ ВИДНО ЛУЧШЕ"
        title="Разные отели. Ваши приоритеты."
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
        <span>{ids.length} из 4 отелей</span>
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
                        {d.characteristics.find((s) => s.id === id)?.name_ru ||
                          names[id] ||
                          id}
                        {parsed?.must.some(
                          (c) => c.characteristic_id === id,
                        ) && <span className="row-required">Обязательно</span>}
                      </th>
                      {d.hotels.map((h) => {
                        const facts = h.facts.filter(
                          (f) => f.characteristic_id === id,
                        );
                        const verdict = h.verdicts.find(
                          (v) => v.characteristic_id === id,
                        );
                        return (
                          <td
                            className={
                              verdict?.status === "yes" ? "compare-best" : ""
                            }
                            key={h.hotel.id}
                          >
                            {facts.length ? (
                              facts.map((f) => (
                                <div className="compare-fact" key={f.id}>
                                  <FactValue fact={f} />
                                  <small>{scopeText(f.scope)}</small>
                                  <EvidenceButton fact={f} claims={h.claims} />
                                  {f.status === "unknown" && (
                                    <AskButton
                                      compact
                                      hotelId={h.hotel.id}
                                      characteristicId={id}
                                    />
                                  )}
                                </div>
                              ))
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
