import { useState } from "react";
import { ArrowUpRight } from "lucide-react";
import { Link } from "react-router-dom";
import { useResource } from "../api";
import { roomProfiles, useRoomEvidence } from "../roomApi";
import type { RoomProfile, RoomScope } from "../types";
import { AskButton, ErrorState, EvidenceModal, Synthetic } from "./ui";
import {
  roomEvidenceScope,
  roomLabParams,
  roomStatus,
  roomSymbol,
} from "./group";

export function RoomDescription({
  hotelId,
  profile,
  unknownFloors = false,
  showCopy = true,
}: {
  hotelId: string;
  profile: RoomProfile;
  unknownFloors?: boolean;
  showCopy?: boolean;
}) {
  const { evidence, busy, error, showEvidence, closeEvidence, retryEvidence } =
    useRoomEvidence(
      hotelId,
      Object.fromEntries(
        profile.clauses.map((clause, i) => [
          clause.characteristic_id || String(i),
          clause.fact_ids,
        ]),
      ),
    );
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
      {showCopy && (
        <>
          <p className="room-profile-copy">{profile.description}</p>
          {profile.clauses.some((clause) => clause.synthetic) && (
            <Synthetic>Есть синтетические данные</Synthetic>
          )}
        </>
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
              <div className="room-clause-actions">
                {clause.fact_ids.map((factId, index) => (
                  <button
                    className="text-button small"
                    key={factId}
                    disabled={busy}
                    onClick={() =>
                      showEvidence(clause.characteristic_id || String(i), index)
                    }
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
        <details className="room-caveats-details">
          <summary>Ограничения расчёта · {profile.caveats.length}</summary>
          <ul className="room-caveats">
            {profile.caveats.map((text, i) => (
              <li key={i}>{text}</li>
            ))}
          </ul>
        </details>
      )}
      <ErrorState error={error} retry={retryEvidence} />
      {evidence && (
        <EvidenceModal
          fact={evidence.fact}
          claims={evidence.claims}
          onClose={closeEvidence}
        />
      )}
    </div>
  );
}

const HIGHLIGHTS: Record<string, string> = {
  sea_view: "Вид на море",
  mountain_view: "Горы",
  quiet_night: "Тихо ночью",
  sunset_view: "Закат",
  morning_sun: "Утреннее солнце",
};
/** Confirmed strengths of a position, in the order guests usually ask. */
function highlights(profile: RoomProfile) {
  return Object.keys(HIGHLIGHTS)
    .filter((id) =>
      profile.clauses.some(
        (c) => c.characteristic_id === id && c.status === "yes",
      ),
    )
    .map((id) => HIGHLIGHTS[id]);
}
function sortByHighlights(profiles: RoomProfile[]) {
  const weight = (p: RoomProfile) =>
    highlights(p).length +
    (highlights(p).includes(HIGHLIGHTS.sea_view) ? 2 : 0);
  return [...profiles].sort((a, b) => weight(b) - weight(a));
}
function positionTitle(profile: RoomProfile) {
  const text = profile.title.replace(/^Модельное положение:\s*/, "");
  return text.charAt(0).toUpperCase() + text.slice(1);
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
      {sortByHighlights(current).map((profile) => (
        <details className="room-profile-card" key={profile.id}>
          <summary>
            <h3>{positionTitle(profile)}</h3>
            {highlights(profile).length > 0 && (
              <p className="room-highlights">
                {highlights(profile).map((label) => (
                  <span key={label}>{label}</span>
                ))}
              </p>
            )}
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
            showCopy={false}
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
