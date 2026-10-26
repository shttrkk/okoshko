import { useEffect, useRef, useState } from "react";
import * as maplibregl from "maplibre-gl";
import mapWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";
import type {
  GeoJSONSource,
  Map as LibreMap,
  StyleSpecification,
} from "maplibre-gl";
import { PMTiles, Protocol } from "pmtiles";
import { Compass, Expand, Layers3, MapPin, Minus, Plus } from "lucide-react";
import "maplibre-gl/dist/maplibre-gl.css";
import { api } from "../api";
import type { Geo, Hotel } from "../types";
import { useApp } from "../state";
import { Empty, Modal } from "./ui";
const empty: GeoJSON.FeatureCollection = {
  type: "FeatureCollection",
  features: [],
};
maplibregl.setWorkerUrl(mapWorkerUrl);
let registered = false;
const protocol = new Protocol();
function baseStyle(dark: boolean): StyleSpecification {
  return {
    version: 8,
    sources: {},
    layers: [
      {
        id: "background",
        type: "background",
        paint: { "background-color": dark ? "#162f36" : "#b7d5d5" },
      },
    ],
  };
}
const geoCache = new Map<string, Promise<GeoJSON.FeatureCollection | null>>();
function getGeoJSON(path: string): Promise<GeoJSON.FeatureCollection | null> {
  const cached = geoCache.get(path);
  if (cached) return cached;
  const pending = fetchGeoJSON(path);
  geoCache.set(path, pending);
  return pending;
}
async function fetchGeoJSON(
  path: string,
): Promise<GeoJSON.FeatureCollection | null> {
  try {
    const r = await fetch(path);
    if (!r.ok) return null;
    const j = await r.json();
    return j.type === "FeatureCollection"
      ? j
      : j.type === "Feature"
        ? { type: "FeatureCollection", features: [j] }
        : null;
  } catch {
    return null;
  }
}
function put(map: LibreMap, id: string, data: GeoJSON.GeoJSON) {
  const source = map.getSource(id) as GeoJSONSource | undefined;
  if (source) source.setData(data);
  else map.addSource(id, { type: "geojson", data });
}
function safeLayer(
  map: LibreMap,
  layer: maplibregl.LayerSpecification,
  before?: string,
) {
  if (!map.getLayer(layer.id)) map.addLayer(layer, before);
}
function drawGeo(
  map: LibreMap,
  geo: Geo | null,
  layers: string[],
  floor?: number,
  facade?: string,
  month = 9,
) {
  const facades: GeoJSON.FeatureCollection = {
    type: "FeatureCollection",
    features: (geo?.facades || [])
      .filter((f) => !facade || f.code === facade)
      .map((f) => ({
        type: "Feature",
        geometry: f.geometry,
        properties: { code: f.code, noise: f.night_db_max ?? null },
      })),
  };
  const cones: GeoJSON.FeatureCollection = {
    type: "FeatureCollection",
    features: (geo?.view_cones?.features || []).filter((f) => {
      const p = f.properties || {};
      return (
        (!facade || !p.facade || p.facade === facade) &&
        (!floor || !p.min_floor || floor >= p.min_floor)
      );
    }),
  };
  const sun = geo?.sun_paths
    ?.find((s) => s.month === month)
    ?.points.filter((p) => p.elevation_deg >= 0)
    .at(-1);
  let sunset: GeoJSON.FeatureCollection = empty;
  if (sun && geo?.center) {
    const [lon, lat] = geo.center,
      r = (sun.azimuth_deg * Math.PI) / 180,
      d = 0.014;
    sunset = {
      type: "FeatureCollection",
      features: [
        {
          type: "Feature",
          properties: {},
          geometry: {
            type: "LineString",
            coordinates: [
              [lon, lat],
              [
                lon + (Math.sin(r) * d) / Math.cos((lat * Math.PI) / 180),
                lat + Math.cos(r) * d,
              ],
            ],
          },
        },
      ],
    };
  }
  const building = geo?.building
    ? { type: "FeatureCollection" as const, features: [geo.building] }
    : empty;
  for (const [id, data] of [
    ["selected-building", building],
    ["facades", facades],
    ["cones", cones],
    ["walk-route", geo?.route || empty],
    ["crossings", geo?.crossings || empty],
    ["sunset", sunset],
  ] as const)
    put(map, id, data);
  safeLayer(map, {
    id: "selected-building",
    type: "fill-extrusion",
    source: "selected-building",
    paint: {
      "fill-extrusion-color": "#f26143",
      "fill-extrusion-height": ["coalesce", ["get", "height"], 10],
      "fill-extrusion-opacity": 0.85,
    },
  });
  safeLayer(map, {
    id: "cones-fill",
    type: "fill",
    source: "cones",
    paint: {
      "fill-color": [
        "match",
        ["get", "target"],
        "mountains",
        "#8a9d73",
        "#ecac52",
      ],
      "fill-opacity": 0.2,
    },
  });
  safeLayer(map, {
    id: "cones-line",
    type: "line",
    source: "cones",
    paint: {
      "line-color": "#d99943",
      "line-width": 1.2,
      "line-dasharray": [4, 3],
    },
  });
  safeLayer(map, {
    id: "facades-line",
    type: "line",
    source: "facades",
    paint: {
      "line-color": [
        "case",
        ["==", ["get", "noise"], null],
        "#828d93",
        ["<", ["get", "noise"], 45],
        "#4c9c7a",
        ["<", ["get", "noise"], 55],
        "#e2a644",
        "#d6624e",
      ],
      "line-width": 7,
      "line-opacity": 0.95,
    },
  });
  safeLayer(map, {
    id: "route-line",
    type: "line",
    source: "walk-route",
    paint: {
      "line-color": "#37807e",
      "line-width": 4,
      "line-dasharray": [1, 0.8],
    },
  });
  safeLayer(map, {
    id: "crossings-circle",
    type: "circle",
    source: "crossings",
    paint: {
      "circle-radius": 6,
      "circle-color": "#cc4f3b",
      "circle-stroke-color": "#fff6e9",
      "circle-stroke-width": 2,
    },
  });
  safeLayer(map, {
    id: "sunset-line",
    type: "line",
    source: "sunset",
    paint: {
      "line-color": "#e08233",
      "line-width": 3,
      "line-dasharray": [4, 2],
    },
  });
  for (const [group, ids] of Object.entries({
    view: ["cones-fill", "cones-line"],
    noise: ["facades-line"],
    route: ["route-line", "crossings-circle"],
    sun: ["sunset-line"],
  }))
    for (const id of ids)
      map.setLayoutProperty(
        id,
        "visibility",
        layers.includes(group) ? "visible" : "none",
      );
}
export function TravelMap({
  hotels = [],
  activeId,
  onHover,
  onSelect,
  geo: providedGeo,
  hero = false,
  large = false,
  floor,
  facade,
  month = 9,
}: {
  hotels?: Hotel[];
  activeId?: string | null;
  onHover?: (id: string | null) => void;
  onSelect?: (id: string) => void;
  geo?: Geo | null;
  hero?: boolean;
  large?: boolean;
  floor?: number;
  facade?: string;
  month?: number;
}) {
  const { theme } = useApp();
  const container = useRef<HTMLDivElement>(null),
    mapRef = useRef<LibreMap | null>(null),
    markers = useRef<maplibregl.Marker[]>([]);
  const [ready, setReady] = useState(false),
    [error, setError] = useState(""),
    [base, setBase] = useState("Загружаем локальную карту"),
    [layers, setLayers] = useState(["view", "noise", "route", "sun"]),
    [geo, setGeo] = useState<Geo | null>(providedGeo || null),
    [full, setFull] = useState(false);
  const callbacks = useRef({ onHover, onSelect });
  callbacks.current = { onHover, onSelect };
  const initialCenter = useRef<[number, number]>(
    providedGeo?.center ||
      (hotels[0] ? [hotels[0].lon, hotels[0].lat] : [39.7233, 43.5855]),
  );
  useEffect(() => {
    if (!container.current) return;
    if (!registered) {
      maplibregl.addProtocol("pmtiles", protocol.tile);
      registered = true;
    }
    let disposed = false;
    const dark = theme === "dark";
    let map: LibreMap;
    try {
      map = new maplibregl.Map({
        container: container.current,
        style: baseStyle(dark),
        center: initialCenter.current,
        zoom: hero ? 13.4 : 14.2,
        pitch: hero ? 57 : 48,
        bearing: -22,
        attributionControl: false,
        maxPitch: 70,
        canvasContextAttributes: { antialias: true },
      });
    } catch {
      setError(
        "3D-карта недоступна в этом браузере. Все факты доступны в списке.",
      );
      return;
    }
    mapRef.current = map;
    map.addControl(
      new maplibregl.AttributionControl({
        compact: true,
        customAttribution: "© OpenStreetMap contributors · ODbL",
      }),
      "bottom-right",
    );
    map.on("error", () => {
      /* A missing optional local layer must not hide valid map data. */
    });
    map.on("load", async () => {
      const data = await Promise.all(
        ["coastline", "buildings", "roads", "beaches", "pois", "railways"].map(
          (n) => getGeoJSON(`/data/seed/${n}.geojson`),
        ),
      );
      if (disposed) return;
      const [coast, buildings, roads, beaches, pois, railways] = data;
      if (coast) {
        put(map, "coastline", coast);
        safeLayer(map, {
          id: "land",
          type: "fill",
          source: "coastline",
          filter: ["==", ["geometry-type"], "Polygon"],
          paint: { "fill-color": dark ? "#343d36" : "#e4e3d3" },
        });
        safeLayer(map, {
          id: "coast",
          type: "line",
          source: "coastline",
          paint: {
            "line-color": dark ? "#567e78" : "#82ada2",
            "line-width": 2,
          },
        });
      }
      if (beaches) {
        put(map, "beaches", beaches);
        safeLayer(map, {
          id: "beaches",
          type: "fill",
          source: "beaches",
          paint: { "fill-color": "#d6bb83", "fill-opacity": 0.8 },
        });
      }
      if (roads) {
        put(map, "roads", roads);
        safeLayer(map, {
          id: "road-casing",
          type: "line",
          source: "roads",
          paint: {
            "line-color": dark ? "#56594f" : "#c5c4b5",
            "line-width": 6,
          },
        });
        safeLayer(map, {
          id: "roads",
          type: "line",
          source: "roads",
          paint: {
            "line-color": dark ? "#777a6b" : "#f7f2e5",
            "line-width": 3.5,
          },
        });
      }
      if (railways) {
        put(map, "railways", railways);
        safeLayer(map, {
          id: "railways",
          type: "line",
          source: "railways",
          paint: {
            "line-color": "#8a8c81",
            "line-width": 2,
            "line-dasharray": [3, 2],
          },
        });
      }
      if (pois) {
        put(map, "pois", pois);
        safeLayer(map, {
          id: "pois",
          type: "circle",
          source: "pois",
          paint: {
            "circle-radius": 2.5,
            "circle-color": "#879d74",
            "circle-opacity": 0.7,
          },
        });
      }
      if (buildings) {
        put(map, "buildings", buildings);
        safeLayer(map, {
          id: "buildings",
          type: "fill-extrusion",
          source: "buildings",
          paint: {
            "fill-extrusion-color": dark ? "#748073" : "#d1cbb7",
            "fill-extrusion-height": [
              "case",
              ["!=", ["get", "height_m"], null],
              ["to-number", ["get", "height_m"], 9],
              ["!=", ["get", "height"], null],
              ["to-number", ["get", "height"], 9],
              ["!=", ["get", "levels"], null],
              ["*", ["to-number", ["get", "levels"], 3], 3],
              ["!=", ["get", "building:levels"], null],
              ["*", ["to-number", ["get", "building:levels"], 3], 3],
              9,
            ],
            "fill-extrusion-base": 0,
            "fill-extrusion-opacity": 0.95,
          },
        });
      }
      setBase(
        data.some(Boolean)
          ? "OSM · неизвестные высоты условные"
          : "Базовые слои пока не загружены",
      );
      setReady(true);
      try {
        const archive = new PMTiles(
          `${window.location.origin}/data/tiles/city.pmtiles`,
        );
        const response = await fetch("/data/tiles/city.pmtiles", {
          method: "HEAD",
        });
        if (!response.ok || disposed) return;
        const meta = (await archive.getMetadata()) as {
          vector_layers?: { id: string }[];
        };
        if (disposed) return;
        protocol.add(archive);
        map.addSource("city", {
          type: "vector",
          url: `pmtiles://${window.location.origin}/data/tiles/city.pmtiles`,
        });
        const baseBefore = map
          .getStyle()
          .layers.find((l) => l.id !== "background")?.id;
        for (const l of meta.vector_layers || []) {
          const isBuilding = /building/.test(l.id),
            isRoad = /road|transport/.test(l.id),
            isWater = /water/.test(l.id),
            isLand = l.id === "earth";
          if (isBuilding && !buildings)
            safeLayer(map, {
              id: `tile-${l.id}`,
              type: "fill-extrusion",
              source: "city",
              "source-layer": l.id,
              paint: {
                "fill-extrusion-color": dark ? "#748073" : "#d1cbb7",
                "fill-extrusion-height": [
                  "coalesce",
                  ["to-number", ["get", "height"]],
                  9,
                ],
              },
            });
          else if (isRoad && !roads)
            safeLayer(map, {
              id: `tile-${l.id}`,
              type: "line",
              source: "city",
              "source-layer": l.id,
              paint: {
                "line-color": dark ? "#7d8270" : "#f5f0e2",
                "line-width": 2,
              },
            });
          else if (isWater || isLand)
            safeLayer(
              map,
              {
                id: `tile-${l.id}`,
                type: "fill",
                source: "city",
                "source-layer": l.id,
                paint: {
                  "fill-color": isWater
                    ? dark
                      ? "#162f36"
                      : "#b7d5d5"
                    : dark
                      ? "#343d36"
                      : "#e4e3d3",
                },
              },
              baseBefore,
            );
        }
        setBase("PMTiles + OSM · часть высот условная");
      } catch {
        /* GeoJSON fallback remains available. */
      }
    });
    return () => {
      disposed = true;
      setReady(false);
      markers.current.forEach((m) => m.remove());
      markers.current = [];
      map.remove();
      mapRef.current = null;
    };
  }, [theme, hero]);
  useEffect(() => {
    if (providedGeo) {
      setGeo(providedGeo);
      return;
    }
    if (!activeId) {
      setGeo(null);
      return;
    }
    let active = true;
    api
      .geo(activeId)
      .then((g) => {
        if (active) setGeo(g);
      })
      .catch(() => {
        if (active) setGeo(null);
      });
    return () => {
      active = false;
    };
  }, [activeId, providedGeo]);
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    markers.current.forEach((m) => m.remove());
    markers.current = hotels.map((hotel, i) => {
      const el = document.createElement("button");
      el.type = "button";
      el.className = `map-marker ${hotel.id === activeId ? "selected" : ""}`;
      el.textContent = hero ? "" : String(i + 1);
      el.title = hotel.name;
      el.setAttribute("aria-label", hotel.name);
      el.dataset.testid = `map-hotel-${hotel.id}`;
      el.onmouseenter = () => callbacks.current.onHover?.(hotel.id);
      el.onmouseleave = () => callbacks.current.onHover?.(null);
      el.onfocus = () => callbacks.current.onHover?.(hotel.id);
      el.onclick = () => callbacks.current.onSelect?.(hotel.id);
      return new maplibregl.Marker({ element: el })
        .setLngLat([hotel.lon, hotel.lat])
        .addTo(map);
    });
  }, [hotels, activeId, ready, hero]);
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    drawGeo(map, geo, layers, floor, facade, month);
    if (geo?.center)
      map.easeTo({ center: geo.center, zoom: 15.8, duration: 900 });
    else if (hotels.length) {
      const b = new maplibregl.LngLatBounds();
      hotels.forEach((h) => b.extend([h.lon, h.lat]));
      map.fitBounds(b, { padding: 80, maxZoom: 14.5, duration: 800 });
    }
  }, [geo, layers, ready, floor, facade, month, hotels]);
  const selected = hotels.find((h) => h.id === activeId);
  return (
    <div
      className={`travel-map ${hero ? "hero-map" : ""} ${large ? "large-map" : ""}`}
      data-testid="travel-map"
    >
      <div className="map-canvas" ref={container} />
      {error && (
        <div className="map-error">
          <MapPin />
          <p>{error}</p>
        </div>
      )}
      {!hero && (
        <>
          <div className="map-label">
            <span className="live-dot" />
            Сочи <span>· 3D</span>
          </div>
          <div className="map-layers" aria-label="Слои карты">
            {[
              ["noise", "Тишина"],
              ["sun", "Закат"],
              ["view", "Вид"],
              ["route", "До моря"],
            ].map(([id, label]) => (
              <button
                key={id}
                aria-pressed={layers.includes(id)}
                className={layers.includes(id) ? "active" : ""}
                onClick={() =>
                  setLayers((old) =>
                    old.includes(id)
                      ? old.filter((x) => x !== id)
                      : [...old, id],
                  )
                }
              >
                {label}
              </button>
            ))}
          </div>
          <div className="map-controls">
            <button
              aria-label="Приблизить"
              onClick={() => mapRef.current?.zoomIn()}
            >
              <Plus size={18} />
            </button>
            <button
              aria-label="Отдалить"
              onClick={() => mapRef.current?.zoomOut()}
            >
              <Minus size={18} />
            </button>
            <button
              aria-label="Север сверху"
              onClick={() => mapRef.current?.easeTo({ bearing: 0, pitch: 0 })}
            >
              <Compass size={18} />
            </button>
            {!large && (
              <button
                aria-label="Развернуть карту"
                onClick={() => setFull(true)}
              >
                <Expand size={18} />
              </button>
            )}
          </div>
          {selected && (
            <div className="map-selected">
              <span className="eyebrow">В фокусе</span>
              <strong>{selected.name}</strong>
              {geo?.limitations?.length ? (
                <span>{geo.limitations[0]}</span>
              ) : (
                <span>Выберите характеристику, чтобы изучить расчёт</span>
              )}
            </div>
          )}
          <div className="map-legend">
            <span>
              <i className="legend-line" />
              Маршрут
            </span>
            <span>
              <i className="legend-cone" />
              Обзор
            </span>
            <span>
              <i className="legend-sun" />
              Закат
            </span>
          </div>
        </>
      )}
      {hero && (
        <>
          <span className="hero-map-label">
            43°35′ с. ш. &nbsp;39°43′ в. д.
          </span>
          <span className="sea-label">Чёрное море</span>
        </>
      )}
      <div className="map-source">
        <Layers3 size={11} />
        {base}
      </div>
      {full && (
        <Modal
          wide
          title="Сочи · карта вашего отдыха"
          onClose={() => setFull(false)}
        >
          <TravelMap
            hotels={hotels}
            activeId={activeId}
            geo={geo}
            large
            onSelect={onSelect}
            month={month}
          />
        </Modal>
      )}
    </div>
  );
}
export function GeoModal({
  hotel,
  geo,
  onClose,
}: {
  hotel: Hotel;
  geo: Geo;
  onClose: () => void;
}) {
  const [floor, setFloor] = useState(geo.monte_carlo?.min_floor_p80 || 1),
    [facade, setFacade] = useState(geo.facades?.[0]?.code || ""),
    [month, setMonth] = useState(9);
  const horizon = geo.horizon || [],
    solar = geo.sun_paths?.find((p) => p.month === month)?.points || [];
  const max = Math.max(
    20,
    ...horizon.map((h) => Math.max(h.terrain_deg || 0, h.buildings_deg || 0)),
    ...solar.map((p) => p.elevation_deg),
  );
  const xy = (az: number, el: number) =>
    `${((az / 360) * 720).toFixed(1)},${(180 - (Math.max(0, el) / max) * 150).toFixed(1)}`;
  const points = (key: "terrain_deg" | "buildings_deg") =>
    horizon
      .filter((h) => h[key] !== null)
      .map((h) => xy(h.azimuth_deg, h[key] || 0))
      .join(" ");
  const mc = geo.monte_carlo;
  return (
    <Modal wide title="От карты — к вашему окну" onClose={onClose}>
      <p className="muted">
        {hotel.name} · геометрия, положение солнца и границы достоверности
      </p>
      <div className="geo-controls">
        <label>
          Фасад
          <select value={facade} onChange={(e) => setFacade(e.target.value)}>
            {geo.facades?.map((f) => (
              <option key={f.code}>{f.code}</option>
            ))}
            {!geo.facades?.length && <option value="">Нет данных</option>}
          </select>
        </label>
        <label>
          Этаж
          <input
            type="number"
            min="1"
            max={hotel.floors || 100}
            value={floor}
            onChange={(e) => setFloor(Math.max(1, Number(e.target.value)))}
          />
        </label>
        <label>
          Месяц
          <select
            value={month}
            onChange={(e) => setMonth(Number(e.target.value))}
          >
            {Array.from({ length: 12 }, (_, i) => (
              <option value={i + 1} key={i}>
                {new Intl.DateTimeFormat("ru", { month: "long" }).format(
                  new Date(2026, i, 1),
                )}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="geo-modal-grid">
        <TravelMap
          hotels={[hotel]}
          activeId={hotel.id}
          geo={geo}
          floor={floor}
          facade={facade}
          month={month}
          large
        />
        <section className="geo-reading">
          <div className="eyebrow">Чувствительность расчёта</div>
          <h3>Вид — это вероятность</h3>
          {mc?.probability !== undefined && mc.probability !== null ? (
            <>
              <div className="probability">
                {Math.round(mc.probability * 100)}
                <span>%</span>
              </div>
              <p>
                Видимость в {mc.iterations || "неизвестном числе"} симуляциях
                неопределённых входных высот.
              </p>
              {mc.min_floor_p80 && (
                <p>
                  80% устойчивости:{" "}
                  <strong>с {mc.min_floor_p80}-го этажа</strong>
                </p>
              )}
            </>
          ) : (
            <Empty title="Вероятность не рассчитана">
              Нет достаточных входных данных для Monte Carlo.
            </Empty>
          )}
          <p className="fine-print">
            Показан сохранённый расчёт отеля. Выбор фасада и этажа фильтрует
            доступные слои, но не запускает новый Monte Carlo.
          </p>
        </section>
      </div>
      <section className="horizon-section">
        <div className="section-heading">
          <div>
            <span className="eyebrow">Взгляд по азимуту</span>
            <h3>Что закрывает горизонт</h3>
          </div>
          <div className="graph-legend">
            <span>● Рельеф</span>
            <span>● Здания</span>
            <span>● Солнце</span>
          </div>
        </div>
        {horizon.length ? (
          <svg
            viewBox="0 0 760 210"
            role="img"
            aria-label={`Профиль горизонта и солнечный путь за месяц ${month}`}
            className="horizon-graph"
          >
            {[0, 90, 180, 270, 360].map((a) => (
              <g key={a}>
                <line
                  x1={20 + a * 2}
                  y1={12}
                  x2={20 + a * 2}
                  y2={180}
                  stroke="currentColor"
                  opacity=".1"
                />
                <text
                  x={20 + a * 2}
                  y={205}
                  textAnchor="middle"
                  fill="currentColor"
                  fontSize="11"
                >
                  {a}°
                </text>
              </g>
            ))}
            <g transform="translate(20 0)">
              <polyline
                points={points("terrain_deg")}
                fill="none"
                stroke="#819b78"
                strokeWidth="2"
              />
              <polyline
                points={points("buildings_deg")}
                fill="none"
                stroke="#686e6b"
                strokeWidth="2"
              />
              {solar.length > 0 && (
                <polyline
                  points={solar
                    .map((p) => xy(p.azimuth_deg, p.elevation_deg))
                    .join(" ")}
                  fill="none"
                  stroke="#df8a43"
                  strokeWidth="3"
                />
              )}
            </g>
          </svg>
        ) : (
          <Empty title="Профиль горизонта пока неизвестен" />
        )}
        {!solar.length && (
          <p className="muted">
            Солнечный путь за выбранный месяц не передан API.
          </p>
        )}
      </section>
      <div className="limitations">
        <h3>Что учитывать</h3>
        {geo.limitations?.length ? (
          geo.limitations.map((l) => <p key={l}>{l}</p>)
        ) : (
          <p>
            API не передал ограничения расчёта. До подтверждения отелем вид из
            конкретного номера не гарантирован.
          </p>
        )}
        <p>
          Наружный шум рассчитан приближённо и не описывает шумоизоляцию окон.
        </p>
      </div>
    </Modal>
  );
}
