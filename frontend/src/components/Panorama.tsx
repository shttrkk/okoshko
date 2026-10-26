import { useState } from "react";
import { ExternalLink } from "lucide-react";
import type { Hotel } from "../types";
import { Modal } from "./ui";
import { SIDE_ORDER, SIDES } from "./facts";
const AZIMUTH: Record<string, number> = {
  N: 0,
  NE: 45,
  E: 90,
  SE: 135,
  S: 180,
  SW: 225,
  W: 270,
  NW: 315,
};
/** Yandex Maps panorama parameters, shared by the embed and the deep link. */
function panoramaQuery(hotel: Hotel, side: string) {
  const point = `${hotel.lon.toFixed(6)},${hotel.lat.toFixed(6)}`;
  return new URLSearchParams({
    ll: point,
    z: "17",
    l: "stv,sta",
    "panorama[point]": point,
    "panorama[direction]": `${AZIMUTH[side] ?? 0},0`,
    "panorama[span]": "120,60",
  }).toString();
}
/**
 * Street-level look in the direction a facade faces. Loaded from Yandex only
 * when opened; it complements the calculation and never replaces it.
 */
export function PanoramaModal({
  hotel,
  side: initialSide = "S",
  onClose,
}: {
  hotel: Hotel;
  side?: string;
  onClose: () => void;
}) {
  const [side, setSide] = useState(initialSide);
  const query = panoramaQuery(hotel, side);
  return (
    <Modal title="Панорама рядом с отелем" onClose={onClose} wide>
      <p className="muted panorama-lead">
        Ближайшая съёмка Яндекс Панорам у «{hotel.name}», повёрнутая в сторону
        фасада. Это вид с улицы или из холла, а не из номера: сверьте его с
        расчётом вида по этажам.
      </p>
      <div className="panorama-sides" role="group" aria-label="Сторона фасада">
        {SIDE_ORDER.map((code) => (
          <button
            key={code}
            className={code === side ? "active" : ""}
            aria-pressed={code === side}
            onClick={() => setSide(code)}
          >
            {SIDES[code]}
          </button>
        ))}
      </div>
      <div className="panorama-frame">
        <iframe
          key={query}
          title={`Яндекс Панорамы: ${hotel.name}, ${SIDES[side]}`}
          src={`https://yandex.ru/map-widget/v1/?${query}`}
          loading="lazy"
          allowFullScreen
          sandbox="allow-scripts allow-same-origin allow-popups allow-popups-to-escape-sandbox"
        />
        <span className="panorama-offline">
          Панорама загружается из Яндекса. Без интернета она недоступна — все
          расчёты О!кна работают и без неё.
        </span>
      </div>
      <a
        className="text-button"
        href={`https://yandex.ru/maps/?${query}`}
        target="_blank"
        rel="noreferrer"
      >
        Открыть в Яндекс Картах <ExternalLink size={14} />
      </a>
    </Modal>
  );
}
