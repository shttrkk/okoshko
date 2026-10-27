import { useState } from "react";
import { ArrowRight } from "lucide-react";
import { Link } from "react-router-dom";
import { useResource } from "../api";
import { roomProfiles, useRoomEvidence } from "../roomApi";
import type { RoomProfile, RoomScope } from "../types";
import {
  AskButton,
  ErrorState,
  EvidenceModal,
  scopeText,
  Synthetic,
} from "./ui";
import { RoomAdviceList } from "./RoomAdvice";
import { guestText } from "./panel-guest-presentation";
import {
  roomEvidenceScope,
  roomScopeText,
  roomLabParams,
  roomStatus,
  roomSymbol,
} from "./group";

export function RoomDescription({
  hotelId,
  profile,
  unknownFloors = false,
  showCopy = true,
  requestedIds,
}: {
  hotelId: string;
  profile: RoomProfile;
  unknownFloors?: boolean;
  showCopy?: boolean;
  requestedIds?: string[];
}) {
  const { evidence, busy, error, showEvidence, closeEvidence, retryEvidence } =
    useRoomEvidence(
      hotelId,
      Object.fromEntries(
        profile.clauses.map((clause, i) => [
          `${i}:${clause.characteristic_id || "fact"}`,
          clause.fact_ids,
        ]),
      ),
    );
  const description = requestedIds
    ? profile.clauses
        .filter(
          (clause) =>
            clause.characteristic_id &&
            requestedIds.includes(clause.characteristic_id),
        )
        .slice(0, 2)
        .map((clause) => guestText(clause.text, clause.synthetic))
        .join(" ") || "Данных по вашим пожеланиям пока мало."
    : guestText(
        profile.description,
        profile.clauses.some((clause) => clause.synthetic),
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
            : "Сторона и этаж по расчёту · номер уточнит отель"}
        </span>
      )}
      {showCopy && (
        <>
          <p className="room-profile-copy">{description}</p>
          {profile.clauses.some((clause) => clause.synthetic) && <Synthetic />}
        </>
      )}
      <details className="room-caveats-details room-computed">
        <summary>Для дотошных</summary>
        {requestedIds && (
          <p>
            {guestText(
              profile.description,
              profile.clauses.some((clause) => clause.synthetic),
            )}
          </p>
        )}
        <ul className="room-clauses">
          {profile.clauses.map((clause, i) => (
            <li key={i} data-status={clause.status}>
              <span
                className={`verdict-symbol ${clause.synthetic && clause.status === "yes" ? "partial" : clause.status}`}
                title={roomStatus[clause.status]}
              >
                {clause.status === "yes" ? "≈" : roomSymbol[clause.status]}
              </span>
              <div>
                <span>{guestText(clause.text, clause.synthetic)}</span>{" "}
                {clause.synthetic && <Synthetic />}
                <div className="room-clause-actions">
                  {clause.fact_ids.map((factId, index) => (
                    <button
                      className="text-button small"
                      key={factId}
                      disabled={busy}
                      onClick={() =>
                        showEvidence(
                          `${i}:${clause.characteristic_id || "fact"}`,
                          index,
                        )
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
            <summary>Для дотошных · {profile.caveats.length}</summary>
            <ul className="room-caveats">
              {profile.caveats.map((text, i) => (
                <li key={i}>
                  {guestText(
                    text,
                    profile.clauses.some((clause) => clause.synthetic),
                  )}
                </li>
              ))}
            </ul>
          </details>
        )}
        <ErrorState error={error} retry={retryEvidence} />
      </details>
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
function highlights(profile: RoomProfile, requestedIds?: string[]) {
  return Object.keys(HIGHLIGHTS)
    .filter(
      (id) =>
        (!requestedIds || requestedIds.includes(id)) &&
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

export function RoomHighlights({
  profile,
  requestedIds,
}: {
  profile: RoomProfile;
  requestedIds?: string[];
}) {
  return highlights(profile, requestedIds).length ? (
    <p className="room-highlights">
      {highlights(profile, requestedIds).map((label) => (
        <span key={label}>{label}</span>
      ))}
    </p>
  ) : null;
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
  if (!open) return null;
  if (!scope.facade && !scope.floor_min && !scope.floor_max)
    return <RoomAdviceList hotelId={hotelId} month={scope.month || 9} />;
  return (
    <ScopedRoomProfileList {...{ hotelId, scope, open, link, unknownFloors }} />
  );
}

function ScopedRoomProfileList({
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
  const sorted = sortByHighlights(current);
  const renderProfile = (profile: RoomProfile) => (
    <details className="room-profile-card" key={profile.id}>
      <summary>
        <h3>{positionTitle(profile)}</h3>
        <RoomHighlights profile={profile} />
        <p className="room-profile-preview">
          {guestText(
            profile.description,
            profile.clauses.some((clause) => clause.synthetic),
          )}
        </p>
        {profile.clauses.some((clause) => clause.synthetic) && <Synthetic />}
        <span className="text-button small">Подробности</span>
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
          Проверить окно <ArrowRight size={14} />
        </Link>
      )}
    </details>
  );
  return (
    <div className="room-profiles" data-testid="room-profiles">
      {profiles.loading && (
        <p className="muted" role="status">
          Проверяем стороны и этажи…
        </p>
      )}
      <ErrorState error={profiles.error} retry={profiles.reload} />
      {!profiles.loading && !profiles.error && !current.length && (
        <p className="muted">
          По выбранной стороне и этажу данных мало. Уточните условия у отеля.
        </p>
      )}
      {sorted.slice(0, 4).map(renderProfile)}
      {sorted.length > 4 && (
        <details className="room-other-profiles">
          <summary>Другие стороны и этажи · {sorted.length - 4}</summary>
          <div className="room-profiles">
            {sorted.slice(4).map(renderProfile)}
          </div>
        </details>
      )}
    </div>
  );
}

export function RoomSuggestion({
  hotelId,
  recommendation,
  reason,
  requestedIds,
  profile,
  month,
  unknownFloors = false,
}: {
  hotelId: string;
  recommendation: string;
  reason: string;
  requestedIds?: string[];
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
        <span className="eyebrow">
          {recommendation.startsWith("Просите номер:")
            ? "Просите номер"
            : "Лучший вариант"}
        </span>
        <p>
          {profile
            ? unknownFloors
              ? roomScopeText(profile.scope, true)
              : scopeText(roomEvidenceScope(profile.scope))
            : "Сочетание условий не подтверждено"}
        </p>
        <span className="room-request-reason">
          Потому что: {reason.toLocaleLowerCase("ru")}.
        </span>
        <span className="text-button small">Подробнее о номере</span>
      </summary>
      <p>{recommendation}</p>
      <p className="fine-print">
        Сторону, этаж и наличие номера подтвердит отель.
      </p>
      {open &&
        (profile ? (
          <RoomDescription
            hotelId={hotelId}
            profile={profile}
            requestedIds={requestedIds}
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
