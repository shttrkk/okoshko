import { useState } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, Users } from "lucide-react";
import { useRoomEvidence } from "../roomApi";
import type { RoomPlanItem, RoomRequest, RoomScope, Verdict } from "../types";
import {
  AskButton,
  ErrorState,
  EvidenceModal,
  Synthetic,
  VerdictList,
} from "./ui";
import {
  RoomDescription,
  RoomHighlights,
  RoomProfileList,
} from "./RoomProfiles";
import {
  roomEvidenceScope,
  roomLabParams,
  roomStatus,
  roomSymbol,
} from "./group";
import { SIDES } from "./facts";
import { guestText } from "./panel-guest-presentation";
import { ROOM_CHARACTERISTICS } from "./panel-guest-query";

/** What this person asked for; the matched position is in the recommendation. */
function requestedText(scope?: RoomScope) {
  if (!scope) return "";
  const { facade, floor_min: low, floor_max: high } = scope;
  return [
    facade && SIDES[facade],
    low && high
      ? low === high
        ? `${low}-й этаж`
        : `этажи ${low}–${high}`
      : low
        ? `с ${low}-го этажа`
        : high
          ? `не выше ${high}-го этажа`
          : null,
  ]
    .filter(Boolean)
    .join(" · ");
}

export function GroupPlan({
  hotelId,
  items,
  requests,
  compact = false,
  onOpen,
  unknownFloors = false,
}: {
  hotelId: string;
  items: RoomPlanItem[];
  requests?: RoomRequest[];
  compact?: boolean;
  unknownFloors?: boolean;
  onOpen?: (index: number, item: RoomPlanItem) => void;
}) {
  if (!items.length) return null;
  return (
    <section
      className={`group-plan ${compact ? "compact" : "section-block"}`}
      data-testid="group-plan"
    >
      <div className="group-plan-heading">
        <Users size={20} />
        <h2>{compact ? "План номеров" : "План номеров для компании"}</h2>
      </div>
      {!compact && (
        <p className="muted small">
          У каждого — свои пожелания, отель один. Ниже — какую сторону и этаж
          стоит уточнить для каждого.
        </p>
      )}
      <div className="group-plan-list">
        {items.map((item, index) => (
          <PlanRow
            key={`${index}:${item.label}`}
            {...{ hotelId, item, index, compact, onOpen, unknownFloors }}
            requestedIds={
              requests?.[index]
                ? [...requests[index].must, ...requests[index].nice].map(
                    (criterion) => criterion.characteristic_id,
                  )
                : undefined
            }
            requestedScope={
              requests?.[index]
                ? {
                    ...requests[index].scope,
                    month: requests[index].scope.month || item.scope.month,
                  }
                : undefined
            }
          />
        ))}
      </div>
      {!compact && (
        <p className="fine-print group-availability">
          Вместимость, соседство и количество свободных номеров подтвердит
          отель. Одна сторона и этаж у двоих не подтверждают два свободных
          номера.
        </p>
      )}
    </section>
  );
}
function PlanRow({
  hotelId,
  item,
  index,
  compact,
  onOpen,
  requestedScope,
  requestedIds,
  unknownFloors,
}: {
  hotelId: string;
  item: RoomPlanItem;
  index: number;
  compact: boolean;
  requestedScope?: RoomScope;
  requestedIds?: string[];
  unknownFloors: boolean;
  onOpen?: (index: number, item: RoomPlanItem) => void;
}) {
  const showRoomAdvice =
    requestedIds === undefined ||
    requestedIds.some((id) => ROOM_CHARACTERISTICS.has(id));
  const [open, setOpen] = useState(false);
  const {
    evidence,
    error,
    showEvidence: showProof,
    closeEvidence,
    retryEvidence,
  } = useRoomEvidence(
    hotelId,
    Object.fromEntries(
      item.verdicts.map((v, i) => [
        `${i}:${v.characteristic_id}:${v.label}`,
        v.fact_ids,
      ]),
    ),
  );
  const showEvidence = (verdict: Verdict) => {
    if (verdict.fact_ids.length)
      showProof(
        `${item.verdicts.indexOf(verdict)}:${verdict.characteristic_id}:${verdict.label}`,
      );
  };
  return (
    <article
      className="group-plan-row"
      data-testid={`group-plan-row-${index}`}
      data-status={item.status}
    >
      <div className="group-plan-row-heading">
        <span
          className={`verdict-symbol ${item.status === "yes" ? "partial" : item.status}`}
          aria-label={roomStatus[item.status]}
        >
          {roomSymbol[item.status]}
        </span>
        <div>
          <h3>{item.label}</h3>
          <span className="small muted">
            {[roomStatus[item.status], requestedText(requestedScope)]
              .filter(Boolean)
              .join(" · ")}
          </span>
        </div>
        {showRoomAdvice &&
          (onOpen ? (
            <button
              className="text-button small"
              onClick={() => onOpen(index, item)}
              data-testid={`open-group-room-${index}`}
            >
              Проверить окно <ArrowRight size={14} />
            </button>
          ) : (
            <Link
              className="text-button small"
              to={`/hotel/${encodeURIComponent(hotelId)}?${roomLabParams(item.scope, index)}#room-lab`}
              data-testid={`open-group-room-${index}`}
            >
              Проверить окно <ArrowRight size={14} />
            </Link>
          ))}
      </div>
      <p className="group-recommendation">
        {showRoomAdvice
          ? guestText(
              item.recommendation,
              item.verdicts.some((v) => v.synthetic),
            )
          : "Номер для этой позиции ещё нужно уточнить у отеля."}
      </p>
      {showRoomAdvice && item.profile && (
        <div className="group-profile-preview">
          <RoomHighlights profile={item.profile} requestedIds={requestedIds} />
          {item.profile.clauses.some((clause) => clause.synthetic) && (
            <>
              {" "}
              <Synthetic />
            </>
          )}
        </div>
      )}
      <details
        className="group-plan-details"
        onToggle={(event) => setOpen(event.currentTarget.open)}
      >
        <summary>Подробнее</summary>
        {open && (
          <>
            {item.caveats.length > 0 && (
              <ul className="room-caveats">
                {item.caveats.map((text, i) => (
                  <li key={i}>
                    {guestText(
                      text,
                      item.verdicts.some((v) => v.synthetic),
                    )}
                  </li>
                ))}
              </ul>
            )}
            <VerdictList
              compact={compact}
              hotelId={hotelId}
              verdicts={item.verdicts}
              questionScope={() =>
                roomEvidenceScope(requestedScope || item.scope)
              }
              onEvidence={(verdict) => void showEvidence(verdict)}
            />
            {item.profile ? (
              <RoomDescription
                hotelId={hotelId}
                profile={item.profile}
                requestedIds={requestedIds}
                unknownFloors={unknownFloors}
              />
            ) : (
              <RoomProfileList
                hotelId={hotelId}
                scope={item.scope}
                unknownFloors={unknownFloors}
              />
            )}
            <AskButton
              hotelId={hotelId}
              characteristicId="room_orientation"
              scope={roomEvidenceScope(requestedScope || item.scope)}
              compact
            />
          </>
        )}
      </details>
      <ErrorState error={error} retry={retryEvidence} />
      {evidence && (
        <EvidenceModal
          fact={evidence.fact}
          claims={evidence.claims}
          onClose={closeEvidence}
        />
      )}
    </article>
  );
}
