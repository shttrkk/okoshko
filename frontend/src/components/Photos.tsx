import { ExternalLink } from "lucide-react";
import { record, useResource } from "../api";
import type { Json } from "../types";
export type HotelPhoto = {
  kind: "building" | "official_site";
  url: string;
  local: boolean;
  page_url?: string | null;
  title?: string | null;
  author?: string | null;
  license?: string | null;
  license_url?: string | null;
  match?: string | null;
};
let hotelPhotoIndex: Promise<Record<string, HotelPhoto[]>> | null = null;
/** Photo index built offline by scripts/collect_hotel_photos.py; optional. */
export function hotelPhotos(): Promise<Record<string, HotelPhoto[]>> {
  hotelPhotoIndex ||= fetch("/data/seed/hotel_photos.json")
    .then(async (response) =>
      response.ok ? (await response.json()).photos || {} : {},
    )
    .catch(() => ({}));
  return hotelPhotoIndex;
}
let modelIndex: Promise<Record<string, { url: string }>> | null = null;
/** Rendered views of the open-data 3D model for hotels without a photo. */
export function buildingModels(): Promise<Record<string, { url: string }>> {
  modelIndex ||= fetch("/data/seed/building_snapshots.json")
    .then(async (response) =>
      response.ok ? (await response.json()).models || {} : {},
    )
    .catch(() => ({}));
  return modelIndex;
}
export function photoLabel(photo: HotelPhoto) {
  return photo.kind === "building"
    ? "Фото здания · Wikimedia Commons"
    : "Фото с сайта отеля";
}
export function photoCredit(photo: HotelPhoto) {
  return [photo.title?.replace(/^File:/, ""), photo.author, photo.license]
    .filter(Boolean)
    .join(" · ");
}
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
          const remote = photo.thumbnail || saved?.thumbnail;
          const src =
            typeof local === "string" && local.startsWith("/data/")
              ? local
              : typeof remote === "string" && remote.startsWith("https://")
                ? remote
                : null;
          return (
            <figure key={source || i}>
              {src && (
                <a href={source} target="_blank" rel="noreferrer">
                  <img
                    src={src}
                    alt={String(photo.title || "Фото окрестностей")}
                    loading="lazy"
                    decoding="async"
                    referrerPolicy="no-referrer"
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
