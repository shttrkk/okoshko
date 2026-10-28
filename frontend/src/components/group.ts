import type {
  Criterion,
  ParsedQuery,
  RoomRequest,
  RoomScope,
  Scope,
} from "../types";
import { MONTHS, SIDES } from "./facts";

export const groupExample =
  "Едем компанией: Маше — тихий номер, Игорю — вид на море. Для всех — до пляжа не больше 10 минут";
export const roomStatus = {
  yes: "По расчёту",
  partial: "Примерно",
  no: "Не подходит",
  unknown: "Мало данных",
};
export const roomSymbol = { yes: "≈", partial: "≈", no: "×", unknown: "?" };
export function roomScopeText(scope: RoomScope, unknownFloors = false) {
  return (
    [
      scope.facade && SIDES[scope.facade],
      unknownFloors
        ? "1-й этаж (условно)"
        : scope.floor_min && scope.floor_max
          ? scope.floor_min === scope.floor_max
            ? `${scope.floor_min}-й этаж`
            : `этажи ${scope.floor_min}–${scope.floor_max}`
          : scope.floor_min
            ? `с ${scope.floor_min}-го этажа`
            : scope.floor_max
              ? `не выше ${scope.floor_max}-го этажа`
              : null,
      scope.month && MONTHS[scope.month - 1],
    ]
      .filter(Boolean)
      .join(" · ") || "Сторону и этаж нужно уточнить"
  );
}
export function roomEvidenceScope(scope: RoomScope): Scope {
  return {
    ...scope,
    type: scope.room_type
      ? "room_type"
      : scope.facade
        ? scope.floor_min || scope.floor_max
          ? "floor_range"
          : "facade"
        : "hotel",
  };
}
export function criterionIdentity(c: Criterion) {
  return JSON.stringify([
    c.characteristic_id,
    c.field,
    c.op,
    c.value,
    c.scope,
    !!c.no_stairs,
    !!c.no_crossings,
  ]);
}
/** Only this request and the shared conditions enter the window preview. */
export function personalRoomQuery(
  parsed: ParsedQuery,
  room: RoomRequest,
): ParsedQuery {
  const scoped = (c: Criterion): Criterion => ({
    ...c,
    scope: { ...roomEvidenceScope(room.scope), ...c.scope },
  });
  return {
    ...parsed,
    rooms: [],
    text: `${room.label}: ${[...room.must, ...room.nice].map((c) => c.label).join(", ")}`,
    // Keep shared restrictions visible: requestedScope in RoomLab detects an
    // opposing personal side or a floor cap instead of silently replacing it.
    facade: parsed.facade || room.scope.facade || null,
    min_floor:
      Math.max(parsed.min_floor || 0, room.scope.floor_min || 0) || null,
    month: room.scope.month || parsed.month,
    must: [...parsed.must, ...room.must.map(scoped)],
    nice: [...parsed.nice, ...room.nice.map(scoped)],
    no_data: [...(parsed.no_data || []), ...(room.no_data || []).map(scoped)],
    no_data_must: [
      ...(parsed.no_data_must || []),
      ...(room.no_data_must || []).map(scoped),
    ],
    unmapped: [...parsed.unmapped, ...(room.unmapped || [])],
    clarifications: [
      ...parsed.clarifications,
      ...(room.clarifications || []),
    ].slice(0, 2),
  };
}
export function roomLabParams(scope: RoomScope, roomIndex?: number) {
  const params = new URLSearchParams();
  if (scope.facade) params.set("room_side", scope.facade);
  if (scope.floor_min || scope.floor_max)
    params.set("room_floor", String(scope.floor_min || scope.floor_max));
  if (scope.month) params.set("room_month", String(scope.month));
  if (roomIndex !== undefined) params.set("room_request", String(roomIndex));
  return params;
}
