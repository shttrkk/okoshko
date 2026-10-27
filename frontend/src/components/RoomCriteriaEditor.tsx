import { X } from "lucide-react";
import type { Criterion, RoomRequest } from "../types";
import { criterionIdentity, roomScopeText } from "./group";
import {
  criterionBaseLabel,
  criterionLabel,
  criterionUnit,
} from "./panel-guest-presentation";

export function RoomCriteriaEditor({
  room,
  index,
  onChange,
}: {
  room: RoomRequest;
  index: number;
  onChange: (room: RoomRequest) => void;
}) {
  const unavailable = [
    ...new Map(
      [...(room.no_data || []), ...(room.no_data_must || [])].map(
        (criterion) => [criterionIdentity(criterion), criterion],
      ),
    ).values(),
  ];
  const move = (group: "must" | "nice", at: number) => {
    const target = group === "must" ? "nice" : "must";
    onChange({
      ...room,
      [group]: room[group].filter((_, i) => i !== at),
      [target]: [...room[target], room[group][at]],
    });
  };
  return (
    <section
      className="room-criteria"
      data-testid={`room-criteria-${index}`}
      aria-label={`Условия: ${room.label}`}
    >
      <h3>{room.label}</h3>
      <p className="small muted">{roomScopeText(room.scope)}</p>
      {(["must", "nice"] as const).map((group) => (
        <div className="criteria-group" key={group}>
          <div className="criteria-label">
            {group === "must" ? "Обязательно" : "Желательно"}
            <span>{room[group].length}</span>
          </div>
          <div className="chips">
            {room[group].map((c, at) => (
              <div className={`criterion-chip ${group}`} key={at}>
                <span>
                  {typeof c.value === "number"
                    ? criterionBaseLabel(c)
                    : c.label}
                </span>
                {typeof c.value === "number" && c.no_stairs && (
                  <span className="chip-flag">Без лестниц</span>
                )}
                {typeof c.value === "number" && c.no_crossings && (
                  <span className="chip-flag">Без переходов</span>
                )}
                {typeof c.value === "number" && (
                  <label className="chip-threshold">
                    <span>
                      {c.op === "lte" ? "≤" : c.op === "gte" ? "≥" : "="}
                    </span>
                    <input
                      type="number"
                      min="0"
                      max="9999"
                      aria-label={`${room.label} — порог: ${c.label}`}
                      value={c.value}
                      onChange={(e) =>
                        onChange({
                          ...room,
                          [group]: room[group].map((item, i) =>
                            i === at
                              ? {
                                  ...item,
                                  value: Number(e.target.value),
                                  label: criterionLabel({
                                    ...item,
                                    value: Number(e.target.value),
                                  }),
                                }
                              : item,
                          ),
                        })
                      }
                    />
                    <span>{criterionUnit(c)}</span>
                  </label>
                )}
                <button
                  title={
                    group === "must"
                      ? "Сделать желательным"
                      : "Сделать обязательным"
                  }
                  aria-label={`${room.label} — переместить ${c.label} в ${group === "must" ? "желательные" : "обязательные"}`}
                  onClick={() => move(group, at)}
                >
                  {group === "must" ? "↓" : "↑"}
                </button>
                <button
                  aria-label={`${room.label} — убрать ${c.label}`}
                  onClick={() =>
                    onChange({
                      ...room,
                      [group]: room[group].filter((_, i) => i !== at),
                    })
                  }
                >
                  <X size={12} />
                </button>
              </div>
            ))}
            {!room[group].length && (
              <span className="small muted">Условий нет</span>
            )}
          </div>
        </div>
      ))}
      {!!unavailable.length && (
        <div className="chips criteria-extra">
          {unavailable.map((c: Criterion, at) => {
            const hard = (room.no_data_must || []).some(
              (item) => criterionIdentity(item) === criterionIdentity(c),
            );
            return (
              <span className="criterion-chip no-data-chip" key={at}>
                <span>
                  {c.label} · {hard ? "обязательно" : "желательно"} · нет данных
                </span>
                <button
                  aria-label={`${room.label} — сделать ${c.label} ${hard ? "желательным" : "обязательным"}`}
                  onClick={() =>
                    onChange({
                      ...room,
                      no_data: unavailable,
                      no_data_must: hard
                        ? (room.no_data_must || []).filter(
                            (item) =>
                              criterionIdentity(item) !== criterionIdentity(c),
                          )
                        : [...(room.no_data_must || []), c],
                    })
                  }
                >
                  {hard ? "↓" : "↑"}
                </button>
                <button
                  aria-label={`${room.label} — убрать ${c.label}`}
                  onClick={() =>
                    onChange({
                      ...room,
                      no_data: unavailable.filter(
                        (item) =>
                          criterionIdentity(item) !== criterionIdentity(c),
                      ),
                      no_data_must: (room.no_data_must || []).filter(
                        (item) =>
                          criterionIdentity(item) !== criterionIdentity(c),
                      ),
                    })
                  }
                >
                  <X size={12} />
                </button>
              </span>
            );
          })}
        </div>
      )}
      {!!room.unmapped?.length && (
        <div className="unsupported-chips">
          <p className="fine-print">
            Эти пожелания требуют уточнения. Полное совпадение не подтверждено.
          </p>
          {room.unmapped.map((text, at) => (
            <span className="criterion-chip" key={at}>
              <span>{text}</span>
              <button
                aria-label={`${room.label} — убрать неразобранное пожелание ${text}`}
                onClick={() =>
                  onChange({
                    ...room,
                    clarifications:
                      room.unmapped?.length === 1 ? [] : room.clarifications,
                    unmapped: room.unmapped?.filter((_, i) => i !== at),
                  })
                }
              >
                <X size={12} />
              </button>
            </span>
          ))}
        </div>
      )}
      {room.clarifications?.map((text, i) => (
        <p className="fine-print" key={i}>
          {text}
        </p>
      ))}
    </section>
  );
}
