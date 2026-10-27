import { ArrowUpRight } from "lucide-react";
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

export function RoomAdviceList({
  hotelId,
  month,
  onSelect,
}: {
  hotelId: string;
  month: number;
  onSelect?: (scope: RoomScope) => void;
}) {
  const advice = useResource(
    () => roomAdvice(hotelId, month),
    [hotelId, month],
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
      {advice.data?.slice(0, 4).map((item) => (
        <AdviceCard
          key={item.id}
          hotelId={hotelId}
          advice={item}
          onSelect={onSelect}
        />
      ))}
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
  const primary = advice.options[0];
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
          <ArrowUpRight size={17} />
        </button>
      ) : primary ? (
        <Link className="room-advice-title" to={target(primary.scope)}>
          <h3>{advice.title}</h3>
          <ArrowUpRight size={17} />
        </Link>
      ) : (
        <h3>{advice.title}</h3>
      )}
      <p>{advice.text}</p>
      <div className="room-advice-status">
        {advice.status === "partial" && (
          <span className="badge warning">Предварительно · нужно уточнить</span>
        )}
        {advice.synthetic && <Synthetic />}
      </div>
      <div className="room-advice-options">
        {advice.options.map((option, index) => {
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
                  <ArrowUpRight size={13} />
                </button>
              ) : (
                <Link {...props} to={target(option.scope)}>
                  {label}
                  <ArrowUpRight size={13} />
                </Link>
              )}
              {option.status === "partial" && advice.status !== "partial" && (
                <span className="small muted"> · предварительно</span>
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
        <summary>Как посчитано</summary>
        {advice.caveats.length > 0 && (
          <ul className="room-caveats">
            {advice.caveats.map((text, i) => (
              <li key={i}>{text}</li>
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
        {advice.options.map((option, optionIndex) => (
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
  profile,
  onSelect,
  children,
}: {
  hotelId: string;
  month: number;
  recommendation?: string | null;
  profile?: RoomProfile | null;
  children?: ReactNode;
  onSelect: (scope: RoomScope) => void;
}) {
  return (
    <section
      className="section-block hotel-room-advice"
      data-testid="hotel-room-profiles"
    >
      <span className="eyebrow">У КАЖДОЙ СТОРОНЫ СВОЙ ХАРАКТЕР</span>
      <h2>Какой номер просить</h2>
      {recommendation && (
        <div className="room-request-advice">
          <span className="eyebrow">ПОД ВАШ ЗАПРОС</span>
          <p className="room-recommendation" data-testid="room-recommendation">
            {recommendation}
          </p>
          {children}
          {profile?.scope.facade && (
            <button
              className="text-button small"
              onClick={() => onSelect(profile.scope)}
            >
              Проверить эту сторону и этаж <ArrowUpRight size={14} />
            </button>
          )}
        </div>
      )}
      <p className="muted section-lead">
        {recommendation
          ? "Если важнее что-то другое"
          : "Выберите, что важно для вашего отдыха"}
      </p>
      <RoomAdviceList hotelId={hotelId} month={month} onSelect={onSelect} />
      <p className="fine-print room-stock-note">
        Советы относятся к сторонам здания и этажам. Реальный номер,
        расположение окон и наличие мест подтвердит отель.
      </p>
    </section>
  );
}
