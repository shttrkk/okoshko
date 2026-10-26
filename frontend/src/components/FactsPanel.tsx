import type { ReactNode } from "react";
import { Eye, MapPin } from "lucide-react";
import { record } from "../api";
import type { Claim, Fact, Scope } from "../types";
import { NearbyPhotos } from "./Photos";
import {
  AskButton,
  EvidenceButton,
  FactValue,
  names,
  needsQuestion,
  scopeText,
  Synthetic,
} from "./ui";
import {
  fmt,
  factCardId,
  humanFact,
  MONTHS_IN,
  MONTHS_SHORT,
  noiseLabel,
  pct,
  SIDE_ORDER,
  SIDES,
  SIDES_SHORT,
  TRAIT_NAMES,
} from "./facts";
type Spec = { id: string; name_ru?: string; category?: string };
const CATEGORY_ORDER = [
  "Вид",
  "Сон",
  "Шум",
  "Рядом",
  "Атмосфера",
  "Доступ",
  "Сезон",
  "Работа",
  "Номер",
  "Окружение",
];
const SOLAR = new Set(["morning_sun", "evening_heat"]);
const WIDE = new Set(["sunset_view", "climate", "nearby_photos", ...SOLAR]);
const VIEWS = new Set(["sunset_view", "sea_view", "mountain_view"]);
const MAPPABLE = new Set([
  ...VIEWS,
  ...SOLAR,
  "quiet_night",
  "railway_noise",
  "beach_walk",
  "night_pharmacy",
  "family_nearby",
  "car_access",
  "beach_surface",
  "accessible_route",
  "green_nearby",
]);
const v = (f: Fact) => record(f.value);
const known = (f: Fact) => f.status !== "unknown" && f.value !== null;
const reliable = (f: Fact) =>
  ["computed", "confirmed"].includes(f.status) && f.value !== null;
const floorOf = (f: Fact) => {
  const value = v(f).min_floor ?? f.scope.floor_min;
  return typeof value === "number" ? value : null;
};
const prob = (f: Fact) =>
  typeof v(f).probability === "number" ? (v(f).probability as number) : 0;
function bySide(facts: Fact[]) {
  const sides = SIDE_ORDER.filter((s) =>
    facts.some((f) => (f.scope.facade || v(f).facade) === s),
  );
  return sides.map((side) => {
    const own = facts.filter((f) => (f.scope.facade || v(f).facade) === side);
    const yes = own
      .filter((f) => reliable(f) && v(f).available === true)
      .sort((a, b) => (floorOf(a) ?? 0) - (floorOf(b) ?? 0));
    const best = yes[0] || [...own].sort((a, b) => prob(b) - prob(a))[0];
    return { side, own, yes, best };
  });
}
function sideList(rows: ReturnType<typeof bySide>) {
  return rows
    .filter((r) => r.yes.length)
    .map(
      (r) =>
        `${SIDES[r.side]}${floorOf(r.best) && floorOf(r.best)! > 1 ? ` — с ${floorOf(r.best)}-го этажа` : ""}`,
    )
    .join(", ");
}
function SunsetMatrix({
  facts,
  month,
  onEvidence,
}: {
  facts: Fact[];
  month: number;
  onEvidence: (f: Fact) => void;
}) {
  const sides = SIDE_ORDER.filter((s) =>
    facts.some((f) => f.scope.facade === s),
  );
  const cell = (side: string, m: number) => {
    const own = facts.filter(
      (f) =>
        f.scope.facade === side &&
        (f.scope.month === m ||
          (Array.isArray(v(f).months) &&
            (v(f).months as number[]).includes(m))),
    );
    const yes = own
      .filter((f) => reliable(f) && v(f).available === true)
      .sort((a, b) => (floorOf(a) ?? 0) - (floorOf(b) ?? 0));
    return { best: yes[0] || own.sort((a, b) => prob(b) - prob(a))[0], yes };
  };
  return (
    <div className="sun-matrix-wrap">
      <table className="sun-matrix">
        <thead>
          <tr>
            <th scope="col">
              <span className="visually-hidden">Фасад</span>
            </th>
            {MONTHS_SHORT.map((name, i) => (
              <th
                key={name}
                scope="col"
                className={i + 1 === month ? "current" : ""}
              >
                {name}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sides.map((side) => (
            <tr key={side}>
              <th scope="row" title={SIDES[side]}>
                {SIDES_SHORT[side]}
              </th>
              {MONTHS_SHORT.map((name, i) => {
                const { best, yes } = cell(side, i + 1);
                if (!best)
                  return (
                    <td key={name} className="empty">
                      ·
                    </td>
                  );
                const p = yes.length ? Math.max(...yes.map(prob)) : 0;
                const level = !reliable(best)
                  ? "empty"
                  : !yes.length
                    ? "no"
                    : p >= 0.8
                      ? "high"
                      : p >= 0.5
                        ? "mid"
                        : "low";
                const floor = floorOf(best);
                return (
                  <td
                    key={name}
                    className={`${level} ${i + 1 === month ? "current" : ""}`}
                  >
                    <button
                      onClick={() => onEvidence(best)}
                      title={`${SIDES[side]}, ${MONTHS_IN[i]}: ${humanFact(best) || ""}`}
                      aria-label={`${SIDES[side]}, ${MONTHS_IN[i]}: ${humanFact(best) || ""}`}
                    >
                      {!reliable(best)
                        ? "?"
                        : yes.length
                          ? floor && floor > 1
                            ? `${floor}+`
                            : "✓"
                          : "—"}
                    </button>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="matrix-legend">
        <span>
          <i className="high" />
          виден, вероятность ≥ 80%
        </span>
        <span>
          <i className="mid" />
          50–80%
        </span>
        <span>
          <i className="no" />
          не виден
        </span>
        <span>
          «7+» — с 7-го этажа. Нажмите на клетку, чтобы открыть расчёт.
          Видимость фасада не подтверждает наличие окна.
        </span>
      </p>
    </div>
  );
}
/** These deterministic proxies have no Monte Carlo probability to colour. */
function SolarMatrix({
  facts,
  month,
  onEvidence,
}: {
  facts: Fact[];
  month: number;
  onEvidence: (f: Fact) => void;
}) {
  const sides = SIDE_ORDER.filter((side) =>
    facts.some((f) => f.scope.facade === side),
  );
  return (
    <div
      className="sun-matrix-wrap solar-proxy-matrix"
      data-testid="solar-proxy-matrix"
    >
      <table className="sun-matrix">
        <thead>
          <tr>
            <th scope="col">
              <span className="visually-hidden">Фасад</span>
            </th>
            {MONTHS_SHORT.map((name, i) => (
              <th
                scope="col"
                key={name}
                className={i + 1 === month ? "current" : ""}
              >
                {name}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sides.map((side) => (
            <tr key={side}>
              <th scope="row" title={SIDES[side]}>
                {SIDES_SHORT[side]}
              </th>
              {MONTHS_SHORT.map((name, i) => {
                const own = facts.filter(
                  (f) => f.scope.facade === side && f.scope.month === i + 1,
                );
                if (!own.length)
                  return (
                    <td className="empty" key={name}>
                      ·
                    </td>
                  );
                const yes = own
                  .filter((f) => reliable(f) && v(f).available === true)
                  .sort((a, b) => (floorOf(a) ?? 0) - (floorOf(b) ?? 0));
                const best = yes[0] || own[0];
                const state = !reliable(best)
                  ? "empty"
                  : yes.length
                    ? "proxy-yes"
                    : v(best).available === false
                      ? "no"
                      : "empty";
                const floor = floorOf(best);
                const label = `${SIDES[side]}, ${MONTHS_IN[i]}: ${humanFact(best) || "нет данных"}. ${scopeText(best.scope)}`;
                return (
                  <td
                    key={name}
                    className={`${state} ${i + 1 === month ? "current" : ""}`}
                  >
                    <button
                      onClick={() => onEvidence(best)}
                      title={label}
                      aria-label={label}
                    >
                      {state === "empty"
                        ? "?"
                        : yes.length
                          ? floor && floor > 1
                            ? `${floor}+`
                            : "✓"
                          : "<20"}
                    </button>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="matrix-legend solar-proxy-note">
        Геометрическая оценка на 15-е число месяца: ✓ — не менее 20 минут
        прямого солнца; «7+» — начиная с 7-го этажа в рассчитанном диапазоне.
        «&lt;20» — меньше 20 минут, «?» — требуется уточнение. Нажмите клетку
        для диапазона этажей и значений. Облачность и наличие окна неизвестны;
        индекс экспозиции не измеряет температуру комнаты.
      </p>
    </div>
  );
}
function SideChips({
  facts,
  kind,
  onEvidence,
}: {
  facts: Fact[];
  kind: "view" | "noise" | "rail";
  onEvidence: (f: Fact) => void;
}) {
  return (
    <div className="side-chips">
      {bySide(facts).map(({ side, yes, best }) => {
        const value = v(best);
        const level = !reliable(best)
          ? "unknown"
          : kind === "noise"
            ? String(value.index || "unknown")
            : kind === "rail"
              ? "rail-proxy"
              : yes.length
                ? "yes"
                : "no";
        const detail = !reliable(best)
          ? "нужно уточнить"
          : kind === "noise"
            ? `${noiseLabel[String(value.index)] || "нет данных"}${typeof value.night_db_max === "number" ? ` · до ${fmt(value.night_db_max)} дБ` : ""}`
            : kind === "rail"
              ? `${fmt(value.night_db_min)}–${fmt(value.night_db_max)} дБ · прокси`
              : yes.length
                ? `${floorOf(best) && floorOf(best)! > 1 ? `с ${floorOf(best)} эт.` : "все этажи"} · ${pct(Math.max(...yes.map(prob)))}`
                : value.available === false
                  ? "не виден"
                  : "нет данных";
        return (
          <button
            key={side}
            className={`side-chip ${level}`}
            onClick={() => onEvidence(best)}
            title={humanFact(best) || ""}
          >
            <strong>{SIDES[side]}</strong>
            <span>{detail}</span>
          </button>
        );
      })}
    </div>
  );
}
function ClimateTable({ facts, month }: { facts: Fact[]; month: number }) {
  const at = (m: number) =>
    v(
      facts.find((f) => f.scope.month === m || v(f).month === m) ||
        ({ value: {} } as Fact),
    );
  const rows: [string, string, number][] = [
    ["Воздух, °C", "air_c", 0],
    ["Вода, °C", "water_c", 0],
    ["Солнце, ч", "sunshine_hours", 0],
    ["Осадки, мм", "precipitation_mm", 0],
  ];
  return (
    <div className="sun-matrix-wrap">
      <table className="climate-table">
        <thead>
          <tr>
            <th scope="col">
              <span className="visually-hidden">Показатель</span>
            </th>
            {MONTHS_SHORT.map((name, i) => (
              <th
                key={name}
                scope="col"
                className={i + 1 === month ? "current" : ""}
              >
                {name}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map(([label, key, digits]) => (
            <tr key={key}>
              <th scope="row">{label}</th>
              {MONTHS_SHORT.map((name, i) => (
                <td key={name} className={i + 1 === month ? "current" : ""}>
                  {fmt(at(i + 1)[key], digits)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {typeof at(month).period === "string" && (
        <p className="matrix-legend">
          <span>
            Средние многолетние значения Open-Meteo:{" "}
            {String(at(month).period)
              .replace("air", "воздух")
              .replace("water", "вода")}
          </span>
        </p>
      )}
    </div>
  );
}
export function headline(id: string, facts: Fact[], month: number): ReactNode {
  const shown = facts.filter(reliable);
  if (!shown.length) {
    if (facts.some((f) => f.status === "disputed"))
      return "Источники противоречат друг другу — уточните у отеля.";
    if (facts.some((f) => f.status === "stale"))
      return "Данные требуют перепроверки. Откройте источник и уточните условия.";
    return "Данных недостаточно для вывода.";
  }
  if (SOLAR.has(id)) {
    const inMonth = shown.filter((f) => f.scope.month === month);
    if (!inMonth.length)
      return `Для ${MONTHS_IN[month - 1]} нет геометрической оценки.`;
    const yes = sideList(bySide(inMonth));
    const summary =
      yes ||
      (inMonth.every((f) => v(f).available === false)
        ? "на рассчитанных фасадах меньше 20 минут"
        : "достаточность прямого солнца неизвестна");
    return `${id === "morning_sun" ? "Утреннее" : "Вечернее"} прямое солнце в ${MONTHS_IN[month - 1]}: ${summary}. Геометрическая оценка, не прогноз погоды${id === "evening_heat" ? " и не температура номера" : ""}.`;
  }
  if (id === "sunset_view") {
    const inMonth = shown.filter(
      (f) =>
        f.scope.month === month ||
        (Array.isArray(v(f).months) &&
          (v(f).months as number[]).includes(month)),
    );
    const list = sideList(bySide(inMonth));
    const good = [
      ...new Set(
        shown.filter((f) => v(f).available === true).map((f) => f.scope.month),
      ),
    ]
      .filter((m): m is number => typeof m === "number")
      .sort((a, b) => a - b);
    return list
      ? `В ${MONTHS_IN[month - 1]} модель показывает видимость заката: ${list}.`
      : !inMonth.length || !inMonth.every((f) => v(f).available === false)
        ? `В ${MONTHS_IN[month - 1]} видимость заката неизвестна.`
        : `В ${MONTHS_IN[month - 1]} закат на рассчитанных фасадах не виден.${good.length ? ` Виден в месяцах: ${good.map((m) => MONTHS_SHORT[m - 1]).join(", ")}.` : ""}`;
  }
  if (id === "sea_view" || id === "mountain_view") {
    const list = sideList(bySide(shown));
    const what = id === "sea_view" ? "Море" : "Горы";
    return list
      ? `Модель показывает видимость ${id === "sea_view" ? "моря" : "гор"}: ${list}.`
      : shown.every((f) => v(f).available === false)
        ? `${what === "Море" ? "Моря" : "Гор"} на рассчитанных фасадах не видно.`
        : `Вид на ${id === "sea_view" ? "море" : "горы"} требует уточнения.`;
  }
  if (id === "quiet_night") {
    const quiet = bySide(shown).filter((r) => v(r.best).index === "quiet");
    return quiet.length
      ? `Тише всего ночью: ${quiet.map((r) => SIDES[r.side]).join(", ")}.`
      : "Модель не выделила тихий фасад. Наружный шум не определяет тишину в номере.";
  }
  if (id === "climate") {
    const f = shown.find(
      (x) => x.scope.month === month || v(x).month === month,
    );
    const text = f ? humanFact(f) : null;
    return text
      ? `В ${MONTHS_IN[month - 1]}: ${text[0].toLowerCase()}${text.slice(1)}.`
      : null;
  }
  const rep = shown[0];
  return rep ? humanFact(rep) || <FactValue fact={rep} /> : null;
}
/** The fact that best stands for a characteristic (evidence link, ask). */
export function representative(facts: Fact[], month?: number): Fact {
  const current = month
    ? facts.filter((f) => !f.scope.month || f.scope.month === month)
    : facts;
  const shown = (current.length ? current : facts).filter(reliable);
  return (
    [...shown]
      .filter((f) => v(f).available === true)
      .sort((a, b) => b.confidence - a.confidence)[0] ||
    [...shown].sort((a, b) => b.confidence - a.confidence)[0] ||
    facts[0]
  );
}
export const isKnownFact = known;
function FactCard({
  id,
  name,
  facts,
  claims,
  hotelId,
  month,
  onEvidence,
  canAsk,
  onPanorama,
  onShowOnMap,
  selectedTrait,
  questionScope,
}: {
  id: string;
  name: string;
  facts: Fact[];
  claims: Claim[];
  hotelId: string;
  month: number;
  onEvidence: (f: Fact) => void;
  canAsk: boolean;
  onPanorama?: (side: string) => void;
  onShowOnMap?: (fact: Fact) => void;
  selectedTrait?: string;
  questionScope?: (characteristicId: string, fact?: Fact) => Scope | undefined;
}) {
  const shown = facts.filter(known);
  const rep = representative(facts, month);
  const disputed = facts.some((f) => f.status === "disputed");
  const confidence = Math.max(...shown.map((f) => f.confidence), 0);
  const multi = shown.length > 1;
  const sided = [
    "sea_view",
    "mountain_view",
    "quiet_night",
    "railway_noise",
  ].includes(id);
  const mapSides = bySide(
    facts.filter((f) => !f.scope.month || f.scope.month === month),
  );
  return (
    <article
      id={factCardId(id)}
      tabIndex={-1}
      className={`fact-card ${WIDE.has(id) ? "wide" : ""} ${disputed ? "disputed" : ""} ${selectedTrait === id ? "fact-selected" : ""}`}
      data-testid={`fact-card-${id}`}
      data-characteristic-id={id}
      data-fact-ids={facts.map((f) => f.id).join(" ")}
    >
      <div className="fact-card-head">
        <h4>{name}</h4>
        <span
          className="fact-confidence"
          title="Уверенность модели, не гарантия результата"
        >
          уверенность {pct(confidence)}
        </span>
      </div>
      <p className="fact-headline">{headline(id, facts, month)}</p>
      {id === "sunset_view" && (
        <SunsetMatrix facts={shown} month={month} onEvidence={onEvidence} />
      )}
      {SOLAR.has(id) && (
        <SolarMatrix facts={shown} month={month} onEvidence={onEvidence} />
      )}
      {sided && (
        <SideChips
          facts={shown}
          kind={
            id === "quiet_night"
              ? "noise"
              : id === "railway_noise"
                ? "rail"
                : "view"
          }
          onEvidence={onEvidence}
        />
      )}
      {id === "climate" && <ClimateTable facts={shown} month={month} />}
      {id === "nearby_photos" && <NearbyPhotos value={rep.value} />}
      {multi &&
        !sided &&
        !["sunset_view", "climate", "nearby_photos", ...SOLAR].includes(id) && (
          <ul className="fact-scopes">
            {shown.map((f) => (
              <li key={f.id} data-fact-id={f.id}>
                <span>{scopeText(f.scope)}</span>
                <FactValue fact={f} />
              </li>
            ))}
          </ul>
        )}
      <div className="fact-card-foot">
        <EvidenceButton fact={rep} claims={claims} />
        {shown.some((f) => f.synthetic) && <Synthetic />}
        {canAsk &&
          (disputed ||
            needsQuestion(rep) ||
            SOLAR.has(id) ||
            id === "railway_noise") && (
            <AskButton
              hotelId={hotelId}
              characteristicId={id}
              scope={questionScope?.(id, rep)}
              compact
            />
          )}
        {onPanorama &&
          VIEWS.has(id) &&
          !!(rep.scope.facade || v(rep).facade) && (
            <button
              className="text-button small panorama-link"
              onClick={() =>
                onPanorama(String(rep.scope.facade || v(rep).facade))
              }
            >
              <Eye size={14} />
              Панорама: {SIDES[String(rep.scope.facade || v(rep).facade)]}
            </button>
          )}
      </div>
      {onShowOnMap && MAPPABLE.has(id) && (
        <div className="fact-map-actions">
          <span>
            <MapPin size={13} /> На карте{mapSides.length ? ":" : ""}
          </span>
          {mapSides.length ? (
            mapSides.map(({ side, best }) => (
              <button
                className="text-button small"
                key={side}
                data-testid={`fact-map-${id}-${side}`}
                data-fact-id={best.id}
                onClick={() => onShowOnMap(best)}
                aria-label={`Показать на карте: ${name}, ${SIDES[side]}`}
              >
                {SIDES[side]}
              </button>
            ))
          ) : (
            <button
              className="text-button small"
              data-testid={`fact-map-${id}`}
              onClick={() => onShowOnMap(rep)}
            >
              Показать
            </button>
          )}
        </div>
      )}
    </article>
  );
}
export function FactsPanel({
  facts,
  claims,
  specs,
  hotelId,
  month,
  onEvidence,
  hotelier = false,
  onPanorama,
  onShowOnMap,
  selectedTrait,
  questionScope,
}: {
  facts: Fact[];
  claims: Claim[];
  specs: Spec[];
  hotelId: string;
  month: number;
  onEvidence: (f: Fact) => void;
  /** The hotel's own cabinet: no "ask the hotel" actions. */
  hotelier?: boolean;
  /** Open a street panorama turned towards a facade. */
  onPanorama?: (side: string) => void;
  /** Focus a characteristic and its actual scope on the hotel map. */
  onShowOnMap?: (fact: Fact) => void;
  selectedTrait?: string;
  questionScope?: (characteristicId: string, fact?: Fact) => Scope | undefined;
}) {
  const ids = [...new Set(facts.map((f) => f.characteristic_id))];
  const spec = (id: string) => specs.find((s) => s.id === id);
  const nameOf = (id: string) =>
    spec(id)?.name_ru || names[id] || TRAIT_NAMES[id] || id;
  const isKnown = (id: string) =>
    facts.some((f) => f.characteristic_id === id && known(f));
  const categories = [
    ...new Set(
      ids.filter(isKnown).map((id) => spec(id)?.category || "Окружение"),
    ),
  ].sort(
    (a, b) =>
      ((CATEGORY_ORDER.indexOf(a) + 99) % 99) -
      ((CATEGORY_ORDER.indexOf(b) + 99) % 99),
  );
  const unknown = ids.filter((id) => !isKnown(id));
  return (
    <div className="facts-panel">
      {categories.map((category) => (
        <div className="fact-category" key={category}>
          <h3>{category}</h3>
          <div className="facts-grid">
            {ids
              .filter(
                (id) =>
                  isKnown(id) &&
                  (spec(id)?.category || "Окружение") === category,
              )
              .map((id) => (
                <FactCard
                  key={id}
                  id={id}
                  name={nameOf(id)}
                  facts={facts.filter((f) => f.characteristic_id === id)}
                  claims={claims}
                  hotelId={hotelId}
                  month={month}
                  onEvidence={onEvidence}
                  canAsk={!hotelier}
                  onPanorama={onPanorama}
                  onShowOnMap={onShowOnMap}
                  selectedTrait={selectedTrait}
                  questionScope={questionScope}
                />
              ))}
          </div>
        </div>
      ))}
      {unknown.length > 0 && (
        <div className="fact-category unknown-facts">
          <h3>Пока неизвестно</h3>
          <p className="muted">
            {hotelier
              ? "Этого нет в открытых данных. Ответ на вопрос из очереди выше заполнит профиль для всех гостей."
              : "Открытых данных не хватило. «Неизвестно» — не значит «нет»: вопрос уйдёт отелю, а ответ дополнит профиль для всех гостей."}
          </p>
          <ul>
            {unknown.map((id) => (
              <li
                key={id}
                id={factCardId(id)}
                tabIndex={-1}
                data-characteristic-id={id}
                data-testid={`fact-card-${id}`}
              >
                <span>{nameOf(id)}</span>
                {!hotelier && (
                  <AskButton
                    hotelId={hotelId}
                    characteristicId={id}
                    scope={questionScope?.(id)}
                    compact
                  />
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
