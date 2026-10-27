import type { Claim, Fact } from "../types";
import { record } from "../api";
import { SIDES } from "./facts";
import { EvidenceButton, scopeText, Synthetic } from "./ui";
import { factDemoLabel, reliability } from "./panel-guest-presentation";
import {
  isSupplementalFact,
  landmarkHeightSource,
  landmarkLabel,
  landmarkName,
  tzDetail,
  tzHumanFact,
} from "./tz-facts";

/** Each supplementary fact retains its own source, reliability and scope. */
export function TzFactDetails({
  facts,
  claims,
  month,
  onShowOnMap,
  onOpenWindow,
}: {
  facts: Fact[];
  claims: Claim[];
  month: number;
  onShowOnMap?: (fact: Fact) => void;
  onOpenWindow?: (fact: Fact) => void;
}) {
  const own = facts.filter(
    (fact) =>
      isSupplementalFact(fact) &&
      (!fact.scope.month || fact.scope.month === month),
  );
  if (!own.length) return null;
  const observation = (fact: Fact) => (
    <article key={fact.id} data-fact-id={fact.id} className="tz-observation">
      <p>{tzHumanFact(fact)}</p>
      <p className="muted small">
        {scopeText(fact.scope)} · {reliability(fact.confidence, fact.synthetic)}
      </p>
      <p className="small">{tzDetail(fact)}</p>
      {fact.synthetic && <Synthetic>{factDemoLabel(fact)}</Synthetic>}
      <EvidenceButton fact={fact} claims={claims} />
      {fact.characteristic_id === "landmark_view" && fact.scope.facade && (
        <div className="tz-observation-actions">
          {onShowOnMap && (
            <button
              className="text-button small"
              data-testid="landmark-map"
              onClick={() => onShowOnMap(fact)}
            >
              На карте: {landmarkName(fact)}
            </button>
          )}
          {onOpenWindow && (
            <button
              className="text-button small"
              data-testid="landmark-window"
              onClick={() => onOpenWindow(fact)}
            >
              Проверить окно
            </button>
          )}
        </div>
      )}
      {typeof record(fact.value).unknown_reason === "string" && (
        <details className="room-caveats-details">
          <summary>Для дотошных</summary>
          <p>
            Причина, сохранённая источником:{" "}
            {String(record(fact.value).unknown_reason)}
          </p>
        </details>
      )}
    </article>
  );
  const groups = [
    ...new Set(
      own.map(
        (fact) => fact.scope.landmark_id || record(fact.value).landmark_id,
      ),
    ),
  ];
  return (
    <div className="tz-observations" data-testid="tz-observations">
      {own[0].characteristic_id === "landmark_view"
        ? groups.map((target) => {
            const group = own.filter(
              (fact) =>
                (fact.scope.landmark_id || record(fact.value).landmark_id) ===
                target,
            );
            return (
              <details
                className="room-caveats-details"
                key={String(target)}
                data-landmark-id={String(target)}
              >
                <summary>
                  {landmarkName(group[0])} · по сторонам и этажам
                </summary>
                {group.map(observation)}
              </details>
            );
          })
        : own.map(observation)}
    </div>
  );
}

/** The map shows saved geometry; technical inputs stay in the third layer. */
export function LandmarkMapProof({
  targets,
  rays,
  metadata,
  warnings,
  landmarkId,
  floor,
  scenario,
}: {
  targets: GeoJSON.Feature[];
  rays: GeoJSON.Feature[];
  metadata?: Record<string, unknown>[];
  warnings?: string[];
  landmarkId?: string;
  floor?: number;
  scenario?: boolean;
}) {
  return (
    <details className="map-landmark-proof" data-testid="landmark-map-proof">
      <summary>Достопримечательность · цель и расчёт</summary>
      {targets.length === 0 && (
        <p>Для выбранной цели нет доступной геометрии. Видимость неизвестна.</p>
      )}
      {targets.map((target, index) => (
        <div key={String(target.id || index)}>
          <strong>
            {String(
              target.properties?.name ||
                landmarkLabel(String(target.properties?.landmark_id)),
            )}
          </strong>
          <p>{landmarkHeightSource(target.properties?.height_source)}.</p>
        </div>
      ))}
      <p>
        {rays.length
          ? "Зелёный луч: верх объекта виден по модели. Красный: верх объекта не виден. Это не проверка окна или свободного номера."
          : !floor
            ? "Выберите известный этаж и сторону, чтобы показать сохранённые лучи."
            : "Для этого среза сохранённых лучей нет. Видимость неизвестна."}
      </p>
      {metadata
        ?.filter((item) => !landmarkId || item.landmark_id === landmarkId)
        .map((item, index) => (
          <p key={index}>
            {item.landmark_id === "fisht"
              ? "Высота «Фишта» не подтверждена. Показан только контур, видимость не рассчитана."
              : "Для этой цели не хватает данных о высоте или видимости."}
          </p>
        ))}
      {warnings?.map((warning) => (
        <p key={warning}>{warning}</p>
      ))}
      {scenario && (
        <p>После изменения окружения сохранённые лучи требуют пересчёта.</p>
      )}
      <details>
        <summary>Для дотошных</summary>
        {targets.map((target, index) => (
          <p key={index}>
            Источник высоты:{" "}
            {String(target.properties?.height_source || "неизвестен")}
          </p>
        ))}
        {rays.map((ray, index) => (
          <p key={index} data-testid="landmark-ray-proof">
            {SIDES[String(ray.properties?.facade)] || "Сторона неизвестна"} ·
            этаж {String(ray.properties?.floor)}.
            {ray.properties?.terrain_blocked === true &&
              " Рельеф перекрывает луч."}
            {Array.isArray(ray.properties?.blocking_building_ids) &&
              ray.properties.blocking_building_ids.length > 0 &&
              ` Здания на луче: ${ray.properties.blocking_building_ids.join(", ")}.`}
          </p>
        ))}
      </details>
    </details>
  );
}
