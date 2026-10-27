import { useState } from "react";
import { Images } from "lucide-react";
import { useResource } from "../api";
import type { Hotel, HotelPhoto } from "../types";
import { hotelPhotos, HotelMapPhotosLink, safePhotoUrl } from "./Photos";
import { Modal } from "./ui";

/** The image elements do not exist until the guest opens the gallery. */
export function HotelPhotoActions({ hotel }: { hotel: Hotel }) {
  const index = useResource(hotelPhotos);
  const [open, setOpen] = useState(false);
  const photos = index.data?.[hotel.id] || [];
  const hasCover = !!(
    (hotel.image?.startsWith("/data/") && safePhotoUrl(hotel.image, true)) ||
    photos.some(
      (photo) =>
        photo.kind !== "room" &&
        (photo.kind !== "street" || photo.local) &&
        safePhotoUrl(photo.url, photo.local),
    )
  );
  const address = hotel.address?.trim();
  const hasStreet =
    hotel.tags?.address_precision !== "area" &&
    address &&
    /(?:улиц|(?:^|\s)ул\.|просп|переул|(?:^|\s)пер\.|шоссе|набереж|(?:^|\s)наб\.|бульвар|проезд|тупик|площадь)/iu.test(
      address,
    );
  const mapHint = hasStreet
    ? `На Картах отель может называться иначе — сверьте адрес: ${address}`
    : "На Картах отель может называться иначе — сверьте место на карте";
  const rooms = photos.filter(
    (photo) =>
      photo.kind === "room" &&
      safePhotoUrl(photo.url, photo.local) &&
      (safePhotoUrl(photo.page_url) || safePhotoUrl(hotel.website)),
  );
  return (
    <div className="hotel-photo-actions">
      {!hasCover && (
        <HotelMapPhotosLink hotel={hotel} prominent hint={mapHint} />
      )}
      {rooms.length > 0 && (
        <button
          className="button secondary small"
          data-testid="open-room-photos"
          onClick={() => setOpen(true)}
        >
          <Images size={16} /> Фото номеров ({rooms.length})
        </button>
      )}
      {hasCover && <HotelMapPhotosLink hotel={hotel} />}
      {open && (
        <RoomPhotoGallery
          hotel={hotel}
          photos={rooms.slice(0, 8)}
          total={rooms.length}
          onClose={() => setOpen(false)}
        />
      )}
    </div>
  );
}

function RoomPhotoGallery({
  hotel,
  photos,
  total,
  onClose,
}: {
  hotel: Hotel;
  photos: HotelPhoto[];
  total: number;
  onClose: () => void;
}) {
  const [failed, setFailed] = useState<string[]>([]);
  const available = photos.filter((photo) => !failed.includes(photo.url));
  const source =
    photos.map((photo) => safePhotoUrl(photo.page_url)).find(Boolean) ||
    safePhotoUrl(hotel.website);
  return (
    <Modal title={`Фото номеров · ${hotel.name}`} onClose={onClose} wide>
      <div className="room-photo-gallery" data-testid="room-photo-gallery">
        <p className="muted">С сайта отеля · ваш номер может отличаться</p>
        {total > 8 && <p className="fine-print">Показываем первые 8 фото.</p>}
        {available.length ? (
          <div className="room-photo-grid">
            {available.map((photo, index) => (
              <figure key={photo.url}>
                <img
                  src={photo.url}
                  alt={`${hotel.name}: фото номера ${index + 1}`}
                  loading="lazy"
                  decoding="async"
                  referrerPolicy="no-referrer"
                  onError={() => setFailed((old) => [...old, photo.url])}
                />
              </figure>
            ))}
          </div>
        ) : (
          <p className="notice" role="status">
            Фото номеров не удалось загрузить. Посмотрите их на сайте отеля.
          </p>
        )}
        {source && (
          <a
            className="text-button"
            data-testid="room-photos-source"
            href={source}
            target="_blank"
            rel="noopener noreferrer"
          >
            Номера на сайте отеля ↗
          </a>
        )}
      </div>
    </Modal>
  );
}
