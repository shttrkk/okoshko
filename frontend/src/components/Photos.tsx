import { ExternalLink } from "lucide-react";
import { record, useResource } from "../api";
import type { Json } from "../types";
import type { Hotel, HotelPhoto } from "../types";
export type { HotelPhoto } from "../types";
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
// Same source families blocked by the offline hotel-photo collector.
const blockedPhotoHosts = [
  "booking.com",
  "booking.ru",
  "bstatic.com",
  "ostrovok.ru",
  "zenhotels.com",
  "worldota.net",
  "emergingtravel.com",
  "expedia.com",
  "hotels.com",
  "agoda.com",
  "trip.com",
  "tripadvisor.com",
  "tripadvisor.ru",
  "101hotels.com",
  "101hotels.ru",
  "tvil.ru",
  "sutochno.ru",
  "sutochno.com",
  "otello.ru",
  "bronevik.com",
  "airbnb.com",
  "airbnb.ru",
  "trivago.com",
  "trivago.ru",
  "hotellook.com",
  "hotellook.ru",
  "kayak.com",
  "onetwotrip.com",
  "otpusk.com",
  "tophotels.pro",
  "tophotels.ru",
  "travelata.ru",
  "travelline.ru",
  "level.travel",
  "bnovo.ru",
  "ozon.travel",
  "avito.ru",
  "avito.st",
  "cian.ru",
  "kvartirka.com",
  "facebook.com",
  "fbcdn.net",
  "instagram.com",
  "cdninstagram.com",
  "vk.com",
  "vk.ru",
  "vkuserphoto.ru",
  "userapi.com",
  "ok.ru",
  "mycdn.me",
  "t.me",
  "telegram.me",
  "telegram.org",
  "tiktok.com",
  "pinterest.com",
  "twitter.com",
  "x.com",
  "wa.me",
  "whatsapp.com",
  "youtube.com",
  "youtu.be",
  "ytimg.com",
  "mapillary.com",
  "ya.ru",
  "google.com",
  "googleusercontent.com",
  "business.site",
  "2gis.com",
  "2gis.ru",
  "flamp.ru",
  "irecommend.ru",
  "otzovik.com",
  "zoon.ru",
  "dzen.ru",
  "taplink.cc",
  "taplink.ws",
  "clck.ru",
  "bit.ly",
];
/** External images are limited to ordinary hotel/CDN URLs, never map widgets. */
export function safePhotoUrl(value: unknown, local = false): string | null {
  if (typeof value !== "string") return null;
  if (local) return /^\/data\/[^\\]*$/.test(value) ? value : null;
  const url = safeHttpUrl(value);
  if (!url) return null;
  const host = new URL(url).hostname.toLowerCase();
  if (
    blockedPhotoHosts.some(
      (blocked) => host === blocked || host.endsWith(`.${blocked}`),
    ) ||
    /(?:^|\.)(?:yandex\.[a-z.]+|maps\.[a-z.]+|google\.[a-z.]+|taplink\.[a-z.]+)$/.test(
      host,
    )
  )
    return null;
  return url;
}
export function safeHttpUrl(value: unknown): string | null {
  if (typeof value !== "string") return null;
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) &&
      !url.username &&
      !url.password
      ? url.href
      : null;
  } catch {
    return null;
  }
}
export function yandexHotelUrl(hotel: Pick<Hotel, "name" | "lat" | "lon">) {
  const params = new URLSearchParams({
    text: `${hotel.name} ${hotel.lat}, ${hotel.lon}`,
  });
  return `https://yandex.ru/maps/?${params}`;
}
export function HotelMapPhotosLink({
  hotel,
  className = "text-button small",
}: {
  hotel: Hotel;
  className?: string;
}) {
  return (
    <a
      className={className}
      data-testid="hotel-map-photos-link"
      href={yandexHotelUrl(hotel)}
      target="_blank"
      rel="noopener noreferrer"
    >
      Фото и отзывы на Яндекс Картах ↗
    </a>
  );
}
export function photoLabel(photo: HotelPhoto) {
  if (photo.kind === "street") {
    const year =
      photo.captured_year || /^\d{4}/.exec(photo.captured_at || "")?.[0];
    return `Вид с улицы · Mapillary${photo.author ? ` · ${photo.author}` : ""}${year ? `, ${year}` : ""}`;
  }
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
