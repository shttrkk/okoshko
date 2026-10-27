import type {
  Criterion,
  ParsedQuery,
  QuerySuggestion,
  RoomRequest,
} from "../types";
import { criterionIdentity } from "./group";
import { criterionLabel } from "./panel-guest-presentation";

/** Same room-dependent characteristics as backend query_match. */
export const ROOM_CHARACTERISTICS = new Set([
  "sea_view",
  "sunset_view",
  "mountain_view",
  "quiet_night",
  "soundproofing",
  "room_orientation",
  "morning_sun",
  "evening_heat",
  "railway_noise",
]);
export function requestedCriteria(
  parsed: ParsedQuery | null | undefined,
): Criterion[] {
  if (!parsed) return [];
  const list = [
    ...parsed.must,
    ...parsed.nice,
    ...(parsed.no_data || []),
    ...(parsed.no_data_must || []),
    ...(parsed.rooms || []).flatMap((room) => [
      ...room.must,
      ...room.nice,
      ...(room.no_data || []),
      ...(room.no_data_must || []),
    ]),
  ];
  return [
    ...new Map(
      list.map((criterion) => [criterionIdentity(criterion), criterion]),
    ).values(),
  ];
}
export function requestedRoomCriteria(parsed: ParsedQuery | null | undefined) {
  return requestedCriteria(parsed).filter((criterion) =>
    ROOM_CHARACTERISTICS.has(criterion.characteristic_id),
  );
}
/** A choice changes the editable query only. The caller still requires Apply. */
export function chooseSuggestion<T extends ParsedQuery | RoomRequest>(
  query: T,
  suggestion: QuerySuggestion,
): T {
  const identity = criterionIdentity(suggestion.criterion);
  const group = suggestion.required ? "must" : "nice";
  const other = suggestion.required ? "nice" : "must";
  const existing = query[group].some(
    (criterion) => criterionIdentity(criterion) === identity,
  );
  const suggestions = (query.suggestions || []).filter(
    (item) => item !== suggestion,
  );
  return {
    ...query,
    [group]: existing
      ? query[group]
      : [...query[group], structuredClone(suggestion.criterion)],
    [other]: query[other].filter(
      (criterion) => criterionIdentity(criterion) !== identity,
    ),
    suggestions,
    unmapped: suggestion.fragment
      ? query.unmapped?.filter((fragment) => fragment !== suggestion.fragment)
      : query.unmapped,
  };
}
export function QuerySuggestions({
  suggestions = [],
  onChoose,
}: {
  suggestions?: QuerySuggestion[];
  onChoose: (suggestion: QuerySuggestion) => void;
}) {
  if (!suggestions.length) return null;
  return (
    <div className="query-suggestions" data-testid="query-suggestions">
      <p className="fine-print">
        Можно добавить условие. Выберите подходящее и нажмите «Применить».
      </p>
      <div className="chips">
        {suggestions.map((suggestion, index) => (
          <button
            className="button secondary small"
            type="button"
            key={`${criterionIdentity(suggestion.criterion)}:${index}`}
            data-testid="query-suggestion"
            onClick={() => onChoose(suggestion)}
          >
            {suggestion.label}
          </button>
        ))}
      </div>
    </div>
  );
}

export function mobilityContext(parsed: ParsedQuery | null | undefined) {
  return (
    !!parsed &&
    (/коляск|реб[её]н|дет[еьи]/i.test(parsed.text) ||
      requestedCriteria(parsed).some(
        (criterion) =>
          ["family_nearby", "accessible_route"].includes(
            criterion.characteristic_id,
          ) || criterion.no_stairs,
      ))
  );
}
export function contextualDisappointment(text: string, withMobility: boolean) {
  return withMobility
    ? text
    : text.replace(
        /\s*[—–-]\s*проверьте маршрут, если едете с коляской\.?/i,
        ".",
      );
}

export function roomRequestReason(parsed: ParsedQuery | null | undefined) {
  return [
    ...new Set(
      requestedRoomCriteria(parsed).map((criterion) =>
        criterionLabel(criterion)
          .split(/[≤≥=]/)[0]
          .trim()
          .replace(/: нет$/, " — нет"),
      ),
    ),
  ].join(", ");
}
