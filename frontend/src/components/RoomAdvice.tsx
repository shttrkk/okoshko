import { ArrowRight } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { useResource } from "../api";
import { roomAdvice, useRoomEvidence } from "../roomApi";
import type { RoomAdvice as Advice, RoomScope } from "../types";
import { roomEvidenceScope, roomLabParams } from "./group";
import {
  AskButton,
  ErrorState,
  EvidenceModal,
  scopeText,
  Synthetic,
} from "./ui";
import type { RoomProfile } from "../types";
import { guestText, humanExplanation } from "./panel-guest-presentation";

export function RoomAdviceList({
  hotelId,
  month,
  onSelect,
  profile,
  requestedCharacteristicIds = [],
  collapseGeographic = false,
}: {
  hotelId: string;
  month: number;
  onSelect?: (scope: RoomScope) => void;
  profile?: RoomProfile | null;
  requestedCharacteristicIds?: string[];
  collapseGeographic?: boolean;
}) {
  const advice = useResource(
    () => roomAdvice(hotelId, month),
    [hotelId, month],
  );
  const named =
    advice.data?.filter(
      (item) => item.characteristic_id === "room_orientation",
    ) || [];
  const geographic =
    advice.data?.filter(
      (item) => item.characteristic_id !== "room_orientation",
    ) || [];
  const geographicContent = (
    <>
      {profile && (
        <AdviceCompromise
          hotelId={hotelId}
          advice={geographic}
          profile={profile}
          requestedCharacteristicIds={requestedCharacteristicIds}
          onSelect={onSelect}
        />
      )}
      {geographic.map((item) => (
        <AdviceCard
          key={item.id}
          hotelId={hotelId}
          advice={item}
          onSelect={onSelect}
        />
      ))}
    </>
  );
  return (
    <div className="room-advice-list" data-testid="room-advice">
      {advice.loading && (
        <p className="muted" role="status">
          Проверяем стороны и этажи…
        </p>
      )}
      <ErrorState error={advice.error} retry={advice.reload} />
      {!advice.loading && !advice.error && !advice.data?.length && (
        <div className="room-advice-empty">
          <p className="muted">
            По сторонам здания данных мало — спросите отель.
          </p>
          <AskButton
            hotelId={hotelId}
            characteristicId="room_orientation"
            compact
          />
        </div>
      )}
      {named.map((item) => (
        <AdviceCard key={item.id} hotelId={hotelId} advice={item} />
      ))}
      {collapseGeographic
        ? geographic.length > 0 && (
            <details
              className="room-other-goals"
              data-testid="other-room-goals"
            >
              <summary>Другие варианты по сторонам и этажам</summary>
              {geographicContent}
            </details>
          )
        : geographicContent}
    </div>
  );
}

/** Full coverage of the recommended range, including adjacent options. */
export function adviceCoversScope(advice: Advice, scope: RoomScope) {
  const start = scope.floor_min ?? 1,
    end = scope.floor_max ?? Infinity;
  const intervals = advice.options
    .filter(
      (option) =>
        (!option.scope.facade || option.scope.facade === scope.facade) &&
        (!option.scope.month || option.scope.month === scope.month) &&
        (!option.scope.room_type || option.scope.room_type === scope.room_type),
    )
    .map((option) => [
      option.scope.floor_min ?? 1,
      option.scope.floor_max ?? Infinity,
    ])
    .sort((a, b) => a[0] - b[0]);
  let covered = start - 1;
  for (const [min, max] of intervals) {
    if (max < start || min > end) continue;
    if (min > covered + 1) return false;
    covered = Math.max(covered, max);
  }
  return covered >= end;
}
function AdviceCompromise({
  hotelId,
  advice,
  profile,
  requestedCharacteristicIds,
  onSelect,
}: {
  hotelId: string;
  advice: Advice[];
  profile: RoomProfile;
  requestedCharacteristicIds: string[];
  onSelect?: (scope: RoomScope) => void;
}) {
  const item = profile.scope.facade
    ? advice.find(
        (entry) =>
          requestedCharacteristicIds.includes(entry.characteristic_id) &&
          entry.options.some(
            (option) =>
              option.fact_ids.length > 0 && option.evidence_refs.length > 0,
          ) &&
          !adviceCoversScope(entry, profile.scope),
      )
    : undefined;
  const option = item?.options.find(
    (option) => option.fact_ids.length > 0 && option.evidence_refs.length > 0,
  );
  const { evidence, error, busy, showEvidence, closeEvidence, retryEvidence } =
    useRoomEvidence(hotelId, { compromise: option?.fact_ids || [] });
  if (!item || !option) return null;
  const goal =
    {
      quiet_night: "Для меньшего уличного шума",
      sea_view: "Для вида на море",
      sunset_view: "Для вида на закат",
      mountain_view: "Для вида на горы",
      morning_sun: "Для утреннего солнца",
    }[item.characteristic_id] || item.title;
  return (
    <div
      className="room-advice-compromise"
      data-testid="room-advice-compromise"
      data-characteristic-id={item.characteristic_id}
      data-evidence-refs={JSON.stringify(option.evidence_refs)}
    >
      <p>
        {goal}:{" "}
        <button
          className="text-button small"
          onClick={() => onSelect?.(option.scope)}
        >
          {scopeText(roomEvidenceScope(option.scope))}
        </button>
        ; это отличается от варианта под ваш запрос.
      </p>
      {option.status === "partial" && (
        <span className="badge warning">примерно</span>
      )}
      {option.synthetic && <Synthetic />}
      <button
        className="text-button small"
        disabled={busy || !option.fact_ids.length}
        onClick={() => showEvidence("compromise")}
      >
        Источник <ArrowRight size={12} />
      </button>
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

function AdviceCard({
  hotelId,
  advice,
  onSelect,
}: {
  hotelId: string;
  advice: Advice;
  onSelect?: (scope: RoomScope) => void;
}) {
  const { evidence, error, busy, showEvidence, closeEvidence, retryEvidence } =
    useRoomEvidence(
      hotelId,
      Object.fromEntries([
        ["advice", advice.fact_ids],
        ...advice.options.map((option, index) => [
          `option:${index}`,
          option.fact_ids,
        ]),
      ]),
    );
  const named = advice.characteristic_id === "room_orientation";
  const primary = named ? undefined : advice.options[0];
  const target = (scope: RoomScope) =>
    `/hotel/${encodeURIComponent(hotelId)}?${roomLabParams(scope)}#room-lab`;
  return (
    <article
      className="room-advice-card"
      data-testid="room-advice-card"
      data-advice-id={advice.id}
      data-status={advice.status}
    >
      {primary && onSelect ? (
        <button
          className="room-advice-title"
          onClick={() => onSelect(primary.scope)}
        >
          <h3>{advice.title}</h3>
          <ArrowRight size={17} />
        </button>
      ) : primary ? (
        <Link className="room-advice-title" to={target(primary.scope)}>
          <h3>{advice.title}</h3>
          <ArrowRight size={17} />
        </Link>
      ) : (
        <h3>{advice.title}</h3>
      )}
      <p>
        {named
          ? guestText(advice.text, advice.synthetic)
          : humanExplanation(advice.text, advice.synthetic)}
      </p>
      <div className="room-advice-status">
        {advice.status === "partial" && (
          <span className="badge warning">примерно</span>
        )}
        {advice.synthetic && (
          <Synthetic>{named ? "Ответ отеля · демо" : "Демо"}</Synthetic>
        )}
      </div>
      <div className="room-advice-options">
        {advice.options
          .filter(
            (option) =>
              !!(
                option.scope.facade ||
                option.scope.floor_min ||
                option.scope.floor_max ||
                option.scope.room_type
              ),
          )
          .map((option, index) => {
            const label = scopeText(roomEvidenceScope(option.scope));
            const props = {
              className: "text-button small",
              "data-testid": "room-advice-option",
              "data-scope": JSON.stringify(option.scope),
            };
            return (
              <span key={index}>
                {onSelect ? (
                  <button {...props} onClick={() => onSelect(option.scope)}>
                    {label}
                    <ArrowRight size={13} />
                  </button>
                ) : (
                  <Link {...props} to={target(option.scope)}>
                    {label}
                    <ArrowRight size={13} />
                  </Link>
                )}
                {option.status === "partial" && advice.status !== "partial" && (
                  <span className="small muted"> · примерно</span>
                )}
                {option.synthetic && !advice.synthetic && <Synthetic />}
              </span>
            );
          })}
      </div>
      <details
        className="room-caveats-details"
        data-testid="room-advice-computed"
      >
        <summary>Для дотошных</summary>
        {advice.caveats.length > 0 && (
          <ul className="room-caveats">
            {advice.caveats.map((text, i) => (
              <li key={i}>{guestText(text, advice.synthetic)}</li>
            ))}
          </ul>
        )}
        <div className="room-clause-actions">
          {advice.fact_ids.map((factId, index) => (
            <button
              className="text-button small"
              key={factId}
              disabled={busy}
              onClick={() => showEvidence("advice", index)}
              data-testid="room-advice-evidence"
              data-fact-id={factId}
            >
              Источник {index + 1}
            </button>
          ))}
        </div>
        {!named &&
          advice.options.map((option, optionIndex) => (
            <div className="room-option-proof" key={optionIndex}>
              <p className="small">
                {scopeText(roomEvidenceScope(option.scope))}
              </p>
              <div className="room-clause-actions">
                {option.fact_ids.map((factId, factIndex) => (
                  <button
                    className="text-button small"
                    key={factId}
                    disabled={busy}
                    onClick={() =>
                      showEvidence(`option:${optionIndex}`, factIndex)
                    }
                    data-testid="room-advice-option-evidence"
                    data-fact-id={factId}
                  >
                    Основание {factIndex + 1}
                  </button>
                ))}
              </div>
            </div>
          ))}
        <details className="room-caveats-details">
          <summary>Ссылки на расчёты</summary>
          <ul>
            {[
              ...new Set([
                ...advice.evidence_refs,
                ...advice.options.flatMap((option) => option.evidence_refs),
              ]),
            ].map((ref) => (
              <li key={ref}>
                <code>{ref}</code>
              </li>
            ))}
          </ul>
        </details>
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

export function RoomAdviceSection({
  hotelId,
  month,
  recommendation,
  reason,
  collapsed = false,
  profile,
  requestedCharacteristicIds = [],
  onSelect,
  children,
}: {
  hotelId: string;
  month: number;
  recommendation?: string | null;
  reason?: string;
  collapsed?: boolean;
  profile?: RoomProfile | null;
  requestedCharacteristicIds?: string[];
  children?: ReactNode;
  onSelect: (scope: RoomScope) => void;
}) {
  const content = (
    <>
      <span className="eyebrow">У КАЖДОЙ СТОРОНЫ СВОЙ ХАРАКТЕР</span>
      <h2>Какой номер просить</h2>
      {recommendation && (
        <div className="room-request-advice">
          <span className="eyebrow">ПОД ВАШ ЗАПРОС</span>
          <p className="room-recommendation" data-testid="room-recommendation">
            {humanExplanation(recommendation)}
          </p>
          {reason && (
            <p className="room-request-reason">
              Потому что: {reason.toLocaleLowerCase("ru")}.
            </p>
          )}
          {children}
          {profile?.scope.facade && (
            <button
              className="text-button small"
              onClick={() => onSelect(profile.scope)}
            >
              Проверить эту сторону и этаж <ArrowRight size={14} />
            </button>
          )}
        </div>
      )}
      <RoomAdviceList
        hotelId={hotelId}
        month={month}
        onSelect={onSelect}
        profile={profile}
        requestedCharacteristicIds={requestedCharacteristicIds}
        collapseGeographic={!!recommendation}
      />
      <p className="fine-print room-stock-note">
        Советы относятся к сторонам здания и этажам. Реальный номер,
        расположение окон и наличие мест подтвердит отель.
      </p>
    </>
  );
  return collapsed ? (
    <details
      className="section-block hotel-room-advice"
      data-testid="hotel-room-profiles"
    >
      <summary>Стороны и этажи · по желанию</summary>
      {content}
    </details>
  ) : (
    <section
      className="section-block hotel-room-advice"
      data-testid="hotel-room-profiles"
    >
      {content}
    </section>
  );
}
