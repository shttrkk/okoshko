import { useState } from "react";
import { api, request, useResource } from "./api";
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

/** Keep lazy room proofs attached to the same revision stream as their facts. */
export function useRoomEvidence(
  hotelId: string,
  slots: Record<string, string[]>,
) {
  const [selected, setSelected] = useState<[string, number] | null>(null);
  const factId = selected ? slots[selected[0]]?.[selected[1]] || null : null;
  const proof = useResource(
    () =>
      factId
        ? api
            .factEvidence(hotelId, factId)
            .then((value) => ({ id: factId, value }))
        : Promise.resolve(null),
    [hotelId, factId],
  );
  return {
    evidence:
      !proof.error && proof.data?.id === factId ? proof.data.value : null,
    error: proof.error,
    busy: factId !== null && proof.loading,
    showEvidence: (slot: string, index = 0) => setSelected([slot, index]),
    closeEvidence: () => setSelected(null),
    retryEvidence: proof.reload,
  };
}
