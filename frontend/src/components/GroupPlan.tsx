import { useState } from "react";
import { Link } from "react-router-dom";
import { ArrowUpRight, Users } from "lucide-react";
import { api } from "../api";
import type {
  FactEvidence,
  RoomPlanItem,
  RoomRequest,
  RoomScope,
  Verdict,
} from "../types";
import {
  AskButton,
  ErrorState,
  EvidenceModal,
  Synthetic,
  VerdictList,
} from "./ui";
import { RoomDescription, RoomProfileList } from "./RoomProfiles";
import {
  roomEvidenceScope,
  roomLabParams,
  roomScopeText,
  roomStatus,
  roomSymbol,
} from "./group";

export function GroupPlan({
  hotelId,
  items,
  requests,
  compact = false,
  onOpen,
}: {
  hotelId: string;
  items: RoomPlanItem[];
  requests?: RoomRequest[];
  compact?: boolean;
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
          У каждого — свои пожелания, отель один. Ниже — подходящие положения в
          здании по доступным данным.
        </p>
      )}
      <div className="group-plan-list">
        {items.map((item, index) => (
          <PlanRow
            key={`${index}:${item.label}`}
            {...{ hotelId, item, index, compact, onOpen }}
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
      <p className="fine-print group-availability">
        Количество и наличие номеров уточнит отель. Одинаковая сторона и этаж
        для двух пожеланий не означают два свободных номера. Соседство не
        подтверждено.
      </p>
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
}: {
  hotelId: string;
  item: RoomPlanItem;
  index: number;
  compact: boolean;
  requestedScope?: RoomScope;
  onOpen?: (index: number, item: RoomPlanItem) => void;
}) {
  const [open, setOpen] = useState(false);
  const [evidence, setEvidence] = useState<FactEvidence | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const showEvidence = async (verdict: Verdict) => {
    setError(null);
    if (!verdict.fact_ids.length) return;
    try {
      setEvidence(await api.factEvidence(hotelId, verdict.fact_ids[0]));
    } catch (e) {
      setError(e as Error);
    }
  };
  return (
    <article
      className="group-plan-row"
      data-testid={`group-plan-row-${index}`}
      data-status={item.status}
    >
      <div className="group-plan-row-heading">
        <span
          className={`verdict-symbol ${item.status}`}
          aria-label={roomStatus[item.status]}
        >
          {roomSymbol[item.status]}
        </span>
        <div>
          <h3>{item.label}</h3>
          <span className="small muted">
            {roomStatus[item.status]} ·{" "}
            {roomScopeText(item.scope, item.profile?.conditional)}
          </span>
        </div>
        {onOpen ? (
          <button
            className="text-button small"
            onClick={() => onOpen(index, item)}
            data-testid={`open-group-room-${index}`}
          >
            Проверить окно <ArrowUpRight size={14} />
          </button>
        ) : (
          <Link
            className="text-button small"
            to={`/hotel/${encodeURIComponent(hotelId)}?${roomLabParams(item.scope, index)}#room-lab`}
            data-testid={`open-group-room-${index}`}
          >
            Проверить окно <ArrowUpRight size={14} />
          </Link>
        )}
      </div>
      <p className="group-recommendation">{item.recommendation}</p>
      {item.profile && (
        <p className="group-profile-preview">{item.profile.description}</p>
      )}
      {item.profile?.clauses.some((clause) => clause.synthetic) && (
        <Synthetic>Есть синтетические данные</Synthetic>
      )}
      {item.caveats.length > 0 && (
        <ul className="room-caveats">
          {item.caveats.map((text, i) => (
            <li key={i}>{text}</li>
          ))}
        </ul>
      )}
      <details
        className="group-plan-details"
        onToggle={(event) => setOpen(event.currentTarget.open)}
      >
        <summary>
          {compact ? "Описание и условия" : "Описание положения и основания"}
        </summary>
        {open && (
          <>
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
              <RoomDescription hotelId={hotelId} profile={item.profile} />
            ) : (
              <RoomProfileList hotelId={hotelId} scope={item.scope} />
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
      <ErrorState error={error} />
      {evidence && (
        <EvidenceModal
          fact={evidence.fact}
          claims={evidence.claims}
          onClose={() => setEvidence(null)}
        />
      )}
    </article>
  );
}
