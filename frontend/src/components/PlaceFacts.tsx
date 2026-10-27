import type { ComponentProps } from "react";
import { FactsPanel } from "./FactsPanel";
import { isPlaceTrait } from "./tz-facts";
import "../tz-facts.css";

/** New place observations stay out of the guest's first scan. */
export function PlaceFacts(props: ComponentProps<typeof FactsPanel>) {
  const place = props.facts.filter((fact) =>
    isPlaceTrait(fact.characteristic_id),
  );
  return (
    <>
      <FactsPanel
        {...props}
        facts={props.facts.filter(
          (fact) => !isPlaceTrait(fact.characteristic_id),
        )}
      />
      {place.length > 0 && (
        <details
          className="other-hotel-facts place-facts"
          data-testid="place-facts"
          open={
            props.selectedTrait && isPlaceTrait(props.selectedTrait)
              ? true
              : undefined
          }
        >
          <summary>О месте · транспорт, окружение и сезон</summary>
          <p className="muted">
            Расчёты, известные ограничения и отдельные пробелы в данных.
            «Неизвестно» не означает «нет».
          </p>
          <FactsPanel
            {...props}
            facts={place}
            requestedCharacteristicIds={undefined}
          />
        </details>
      )}
    </>
  );
}
