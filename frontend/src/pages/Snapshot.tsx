import { useParams } from "react-router-dom";
import { useApp } from "../state";
import { BuildingSnapshot } from "../components/Map";
/** Bare render target for the offline building-snapshot script. */
export function SnapshotPage() {
  const { id = "" } = useParams();
  const { hotels } = useApp();
  const hotel = hotels.data?.find((h) => h.id === id);
  return hotel ? (
    <BuildingSnapshot hotel={hotel} />
  ) : (
    <div data-snapshot-status={hotels.data ? "missing" : "loading"} />
  );
}
