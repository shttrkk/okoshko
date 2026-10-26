import { ExternalLink } from "lucide-react";
import { record, useResource } from "../api";
import type { Json } from "../types";
let photoIndex: Promise<Json[]> | null = null;
function localPhotos(): Promise<Json[]> {
  photoIndex ||= fetch("/data/seed/nearby_photos.json")
    .then(async (response) =>
      response.ok ? (await response.json()).photos || [] : [],
    )
    .catch(() => []);
  return photoIndex;
}
export function NearbyPhotos({ value }: { value: unknown }) {
  const index = useResource(localPhotos);
  const photos = record(value).photos;
  if (!Array.isArray(photos) || !photos.length) return null;
  return (
    <div className="nearby-photos">
      <p className="fine-print">
        Геометки Commons. Это фотографии окрестностей; они не доказывают внешний
        вид отеля или вид из номера.
      </p>
      <div className="nearby-photo-grid">
        {photos.slice(0, 6).map((entry, i) => {
          const photo = record(entry),
            source = String(photo.source_url || ""),
            saved = index.data?.find((item) => item.source_url === source);
          const local = photo.local_thumbnail || saved?.local_thumbnail;
          return (
            <figure key={source || i}>
              {typeof local === "string" && local.startsWith("/data/") && (
                <a href={source} target="_blank" rel="noreferrer">
                  <img
                    src={local}
                    alt={String(photo.title || "Фото окрестностей")}
                    loading="lazy"
                  />
                </a>
              )}
              <figcaption>
                <a href={source} target="_blank" rel="noreferrer">
                  {String(photo.title || "Фото окрестностей").replace(
                    /^File:/,
                    "",
                  )}{" "}
                  <ExternalLink size={11} />
                </a>
                <span>
                  {String(photo.author || "Автор не указан")} ·{" "}
                  {String(photo.license || "Лицензия не указана")}
                </span>
              </figcaption>
            </figure>
          );
        })}
      </div>
    </div>
  );
}
