import { request } from "./api";
import type { RoomProfile, RoomScope } from "./types";

export function roomProfiles(hotelId: string, scope: RoomScope = {}) {
  const params = new URLSearchParams();
  for (const key of ["month", "facade", "floor_min", "floor_max"] as const) {
    const value = scope[key];
    if (value !== null && value !== undefined) params.set(key, String(value));
  }
  return request<RoomProfile[]>(
    `/api/hotels/${encodeURIComponent(hotelId)}/rooms?${params}`,
  ).then((profiles) => {
    if (!Array.isArray(profiles))
      throw new Error("API не передал список модельных положений.");
    return profiles;
  });
}
