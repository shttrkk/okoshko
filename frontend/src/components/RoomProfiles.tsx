import { useState } from "react";
import { ArrowUpRight } from "lucide-react";
import { Link } from "react-router-dom";
import { api, useResource } from "../api";
import { roomProfiles } from "../roomApi";
import type { FactEvidence, RoomProfile, RoomScope } from "../types";
import { AskButton, ErrorState, EvidenceModal, Synthetic } from "./ui";
import {
  roomEvidenceScope,
  roomLabParams,
  roomScopeText,
  roomStatus,
  roomSymbol,
} from "./group";

export function RoomDescription({
  hotelId,
  profile,
  unknownFloors = false,
}: {
  hotelId: string;
  profile: RoomProfile;
  unknownFloors?: boolean;
}) {
  const [evidence, setEvidence] = useState<FactEvidence | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const showEvidence = async (factId: string) => {
    setBusy(true);
    setError(null);
    try {
      setEvidence(await api.factEvidence(hotelId, factId));
    } catch (e) {
      setError(e as Error);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div
      className="room-description"
      data-testid="room-description"
      data-profile-id={profile.id}
    >
      {profile.conditional && (
        <span className="room-conditional">
          {unknownFloors
            ? "Условный уровень 1 · этажность не подтверждена"
            : "Модельное положение · номер не подтверждён"}
        </span>
      )}
      <p className="room-profile-copy">{profile.description}</p>
      {profile.clauses.some((clause) => clause.synthetic) && (
        <Synthetic>Есть синтетические данные</Synthetic>
      )}
      <ul className="room-clauses">
        {profile.clauses.map((clause, i) => (
          <li key={i} data-status={clause.status}>
            <span
              className={`verdict-symbol ${clause.status}`}
              title={roomStatus[clause.status]}
            >
              {roomSymbol[clause.status]}
            </span>
            <div>
              <span>{clause.text}</span> {clause.synthetic && <Synthetic />}
              {clause.status === "unknown" && (
                <span className="muted small">
                  {" "}
                  · Нет подтверждённых данных
                </span>
              )}
              <div className="room-clause-actions">
                {clause.fact_ids.map((factId, index) => (
                  <button
                    className="text-button small"
                    key={factId}
                    disabled={busy}
                    onClick={() => void showEvidence(factId)}
                    data-testid="room-clause-evidence"
                    data-fact-id={factId}
                  >
                    {clause.fact_ids.length > 1
                      ? `Основание ${index + 1}`
                      : "Основание"}
                  </button>
                ))}
                {clause.status === "unknown" && clause.characteristic_id && (
                  <AskButton
                    hotelId={hotelId}
                    characteristicId={clause.characteristic_id}
                    scope={roomEvidenceScope(profile.scope)}
                    compact
                  />
                )}
              </div>
            </div>
          </li>
        ))}
      </ul>
      {profile.caveats.length > 0 && (
        <ul className="room-caveats">
          {profile.caveats.map((text, i) => (
            <li key={i}>{text}</li>
          ))}
        </ul>
      )}
      <ErrorState error={error} />
      {evidence && (
        <EvidenceModal
          fact={evidence.fact}
          claims={evidence.claims}
          onClose={() => setEvidence(null)}
        />
      )}
    </div>
  );
}

export function RoomProfileList({
  hotelId,
  scope,
  open = true,
  link = false,
  unknownFloors = false,
}: {
  hotelId: string;
  scope: RoomScope;
  open?: boolean;
  link?: boolean;
  unknownFloors?: boolean;
}) {
  const key = JSON.stringify([hotelId, scope]);
  const profiles = useResource(
    () =>
      open
        ? roomProfiles(hotelId, scope).then((items) => ({ key, items }))
        : Promise.resolve(null),
    [key, open],
  );
  if (!open) return null;
  const current = profiles.data?.key === key ? profiles.data.items : [];
  return (
    <div className="room-profiles" data-testid="room-profiles">
      {profiles.loading && (
        <p className="muted" role="status">
          Собираем описания по фактам…
        </p>
      )}
      <ErrorState error={profiles.error} retry={profiles.reload} />
      {!profiles.loading && !profiles.error && !current.length && (
        <p className="muted">
          Для этого положения пока нет описания. Сторону, этаж и наличие номера
          уточнит отель.
        </p>
      )}
      {current.map((profile) => (
        <details className="room-profile-card" key={profile.id}>
          <summary>
            <h3>{profile.title}</h3>
            <p className="small muted">
              {roomScopeText(profile.scope, unknownFloors)}
            </p>
            <p className="room-profile-preview">{profile.description}</p>
            {profile.clauses.some((clause) => clause.synthetic) && (
              <Synthetic>Есть синтетические данные</Synthetic>
            )}
            <span className="text-button small">Факты и основания</span>
          </summary>
          <RoomDescription
            hotelId={hotelId}
            profile={profile}
            unknownFloors={unknownFloors}
          />
          {link && (
            <Link
              className="text-button small"
              to={`/hotel/${encodeURIComponent(hotelId)}?${roomLabParams(profile.scope)}#room-lab`}
            >
              Проверить окно <ArrowUpRight size={14} />
            </Link>
          )}
        </details>
      ))}
    </div>
  );
}

export function RoomSuggestion({
  hotelId,
  recommendation,
  profile,
  month,
  unknownFloors = false,
}: {
  hotelId: string;
  recommendation: string;
  profile?: RoomProfile | null;
  month: number;
  unknownFloors?: boolean;
}) {
  const [open, setOpen] = useState(false);
  return (
    <details
      className="room-suggestion"
      onToggle={(event) => setOpen(event.currentTarget.open)}
      data-testid="room-suggestion"
    >
      <summary>
        <span className="eyebrow">Просите номер</span>
        <p>{recommendation}</p>
        <span className="text-button small">Описание и основания</span>
      </summary>
      <p className="fine-print">
        Модельное положение. Наличие и количество номеров уточнит отель.
      </p>
      {open &&
        (profile ? (
          <RoomDescription
            hotelId={hotelId}
            profile={profile}
            unknownFloors={unknownFloors}
          />
        ) : (
          <RoomProfileList
            hotelId={hotelId}
            scope={{ month }}
            unknownFloors={unknownFloors}
          />
        ))}
    </details>
  );
}
