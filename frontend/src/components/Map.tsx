import { useEffect, useRef, useState } from "react";
import * as maplibregl from "maplibre-gl";
import mapWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";
import type {
  GeoJSONSource,
  Map as LibreMap,
  StyleSpecification,
} from "maplibre-gl";
import { PMTiles, Protocol } from "pmtiles";
import {
  ArrowRight,
  Compass,
  Expand,
  Layers3,
  MapPin,
  Minus,
  Plus,
} from "lucide-react";
import "maplibre-gl/dist/maplibre-gl.css";
import { api } from "../api";
import type { Geo, Hotel } from "../types";
import { useApp } from "../state";
import { Empty, Modal } from "./ui";
import { meters, roundedMinutes, SIDES } from "./facts";
import { guestText } from "./panel-guest-presentation";
import { landmarkHeightSource, landmarkLabel } from "./tz-facts";
import { LandmarkMapProof } from "./TzFactDetails";
const empty: GeoJSON.FeatureCollection = {
  type: "FeatureCollection",
  features: [],
};
const POI_KINDS: Record<
  string,
  { label: string; symbol: string; trait: string[] }
> = {
  beach: {
    label: "Пляж",
    symbol: "П",
    trait: ["beach_walk", "beach_surface", "accessible_route"],
  },
  pharmacy: { label: "Аптека", symbol: "А", trait: ["night_pharmacy"] },
  parking: { label: "Парковка", symbol: "Р", trait: ["car_access"] },
  playground: { label: "Площадка", symbol: "Д", trait: ["family_nearby"] },
  green: { label: "Парк / сад", symbol: "З", trait: ["green_nearby"] },
};
type NearbyPoint = GeoJSON.Feature<GeoJSON.Point>;
/** Plot only coordinates actually present in the per-hotel response. */
function nearbyPoints(geo: Geo | null): NearbyPoint[] {
  const input: GeoJSON.Feature[] = geo?.nearby?.features || [];
  const features = input
    .filter(
      (f): f is NearbyPoint =>
        f.geometry?.type === "Point" &&
        f.geometry.coordinates.length >= 2 &&
        f.geometry.coordinates.every(
          (n) => typeof n === "number" && Number.isFinite(n),
        ) &&
        Math.abs(f.geometry.coordinates[0]) <= 180 &&
        Math.abs(f.geometry.coordinates[1]) <= 90,
    )
    .map((f) => ({ ...f, properties: { ...f.properties } }));
  for (const feature of features) {
    const kind = String(feature.properties?.kind || "");
    feature.properties!.kind =
      (
        {
          night_pharmacy: "pharmacy",
          family_nearby: "playground",
          car_access: "parking",
          green_nearby: "green",
          park: "green",
          garden: "green",
        } as Record<string, string>
      )[kind] || kind;
  }
  const walk = geo?.route?.features.find(
    (f) => f.geometry.type === "LineString",
  );
  const end =
    walk?.geometry.type === "LineString"
      ? walk.geometry.coordinates.at(-1)
      : null;
  if (end && !features.some((f) => f.properties?.kind === "beach"))
    features.push({
      type: "Feature",
      geometry: { type: "Point", coordinates: end },
      properties: {
        kind: "beach",
        name: guestText(walk?.properties?.beach_name || "ближайший пляж"),
        coordinate_source: "route_endpoint",
      },
    });
  return features.filter((f) => String(f.properties?.kind) in POI_KINDS);
}
maplibregl.setWorkerUrl(mapWorkerUrl);
let registered = false;
const protocol = new Protocol();
function baseStyle(dark: boolean): StyleSpecification {
  return {
    version: 8,
    glyphs: `${window.location.origin}/data/tiles/fonts/{fontstack}/{range}.pbf`,
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
const archives = new Map<string, PMTiles>();
async function attachBaseMap(
  map: LibreMap,
  dark: boolean,
  isDisposed: () => boolean,
): Promise<string> {
  const url = `${window.location.origin}/data/tiles/city.pmtiles`;
  try {
    let archive = archives.get(url);
    if (!archive) {
      archive = new PMTiles(url);
      archives.set(url, archive);
      protocol.add(archive);
    }
    const metadata = (await archive.getMetadata()) as {
      vector_layers?: { id: string }[];
    };
    if (isDisposed()) return "";
    const available = new Set(metadata.vector_layers?.map((l) => l.id));
    if (!available.has("earth") || !available.has("buildings"))
      throw new Error("Неподдерживаемые слои локальной карты");
    map.addSource("city", { type: "vector", url: `pmtiles://${url}` });
    const vectorLayer = (layer: maplibregl.LayerSpecification) =>
      safeLayer(map, layer);
    vectorLayer({
      id: "tile-earth",
      type: "fill",
      source: "city",
      "source-layer": "earth",
      paint: { "fill-color": dark ? "#343d36" : "#e5e4d4" },
    });
    if (available.has("landuse"))
      vectorLayer({
        id: "tile-landuse",
        type: "fill",
        source: "city",
        "source-layer": "landuse",
        paint: {
          "fill-color": [
            "match",
            ["get", "kind"],
            [
              "park",
              "forest",
              "nature_reserve",
              "wood",
              "grass",
              "garden",
              "protected_area",
            ],
            dark ? "#3c4c3b" : "#c3d0b4",
            ["beach", "sand"],
            "#d6c39b",
            dark ? "#3c4237" : "#dddfcc",
          ],
          "fill-opacity": 0.7,
        },
      });
    if (available.has("water"))
      vectorLayer({
        id: "tile-water",
        type: "fill",
        source: "city",
        "source-layer": "water",
        paint: { "fill-color": dark ? "#16343a" : "#b5d3d3" },
      });
    if (available.has("roads")) {
      vectorLayer({
        id: "tile-road-casing",
        type: "line",
        source: "city",
        "source-layer": "roads",
        paint: {
          "line-color": dark ? "#626856" : "#cac9b7",
          "line-width": [
            "interpolate",
            ["linear"],
            ["zoom"],
            11,
            1.2,
            15,
            6,
            18,
            12,
          ],
        },
      });
      vectorLayer({
        id: "tile-roads",
        type: "line",
        source: "city",
        "source-layer": "roads",
        paint: {
          "line-color": [
            "match",
            ["get", "kind"],
            ["highway", "major_road"],
            dark ? "#92917a" : "#faf4df",
            dark ? "#737b68" : "#faf8ee",
          ],
          "line-width": [
            "interpolate",
            ["linear"],
            ["zoom"],
            11,
            0.6,
            15,
            3.8,
            18,
            9,
          ],
        },
      });
      vectorLayer({
        id: "tile-railways",
        type: "line",
        source: "city",
        "source-layer": "roads",
        filter: ["==", ["get", "kind"], "rail"],
        paint: {
          "line-color": dark ? "#a09d85" : "#8c9282",
          "line-width": 2,
          "line-dasharray": [3, 2],
        },
      });
    }
    vectorLayer({
      id: "tile-buildings",
      type: "fill-extrusion",
      source: "city",
      "source-layer": "buildings",
      minzoom: 13,
      paint: {
        "fill-extrusion-color": dark ? "#7b8471" : "#cfc8b1",
        "fill-extrusion-height": [
          "case",
          [">", ["coalesce", ["get", "height"], 0], 0],
          ["get", "height"],
          9,
        ],
        "fill-extrusion-base": ["coalesce", ["get", "min_height"], 0],
        "fill-extrusion-opacity": 0.96,
      },
    });
    if (available.has("roads"))
      vectorLayer({
        id: "tile-road-names",
        type: "symbol",
        source: "city",
        "source-layer": "roads",
        minzoom: 14,
        layout: {
          "symbol-placement": "line",
          "text-field": ["coalesce", ["get", "name:ru"], ["get", "name"], ""],
          "text-font": ["Noto Sans Regular"],
          "text-size": 12,
          "text-max-angle": 30,
          "symbol-spacing": 400,
        },
        paint: {
          "text-color": dark ? "#c5c9b6" : "#7a8273",
          "text-halo-color": dark ? "#343d36" : "#f3f0e0",
          "text-halo-width": 1.3,
        },
      });
    if (available.has("places"))
      vectorLayer({
        id: "tile-place-names",
        type: "symbol",
        source: "city",
        "source-layer": "places",
        layout: {
          "text-field": ["coalesce", ["get", "name:ru"], ["get", "name"], ""],
          "text-font": ["Noto Sans Medium"],
          "text-size": ["interpolate", ["linear"], ["zoom"], 10, 11, 15, 16],
          "text-max-width": 9,
        },
        paint: {
          "text-color": dark ? "#cdd0c1" : "#5e7061",
          "text-halo-color": dark ? "#343d36" : "#e5e4d4",
          "text-halo-width": 1.8,
        },
      });
    return "PMTiles + OSM · часть высот условная";
  } catch {
    if (isDisposed()) return "";
    // Heavy regional GeoJSON is only a fallback. Its data is cached once and
    // only the current viewport is submitted to the renderer.
    const [coast, buildings, roads] = await Promise.all(
      ["coastline", "buildings", "roads"].map((n) =>
        getGeoJSON(`/data/seed/${n}.geojson`),
      ),
    );
    if (isDisposed()) return "";
    if (coast) {
      put(map, "coastline", coast);
      safeLayer(map, {
        id: "fallback-land",
        type: "fill",
        source: "coastline",
        filter: ["==", ["geometry-type"], "Polygon"],
        paint: { "fill-color": dark ? "#343d36" : "#e5e4d4" },
      });
      safeLayer(map, {
        id: "fallback-coast",
        type: "line",
        source: "coastline",
        paint: { "line-color": "#82ada2", "line-width": 2 },
      });
    }
    const indexed = (collection: GeoJSON.FeatureCollection | null) =>
      collection?.features.map((feature) => {
        const points: number[][] = [];
        const collect = (value: unknown) => {
          if (!Array.isArray(value)) return;
          if (typeof value[0] === "number") points.push(value as number[]);
          else value.forEach(collect);
        };
        if ("coordinates" in feature.geometry)
          collect(feature.geometry.coordinates);
        return {
          feature,
          west: Math.min(...points.map((p) => p[0])),
          east: Math.max(...points.map((p) => p[0])),
          south: Math.min(...points.map((p) => p[1])),
          north: Math.max(...points.map((p) => p[1])),
        };
      }) || [];
    const buildingIndex = indexed(buildings),
      roadIndex = indexed(roads);
    const update = () => {
      if (isDisposed()) return;
      const bounds = map.getBounds();
      for (const [name, index] of [
        ["fallback-buildings", buildingIndex],
        ["fallback-roads", roadIndex],
      ] as const)
        put(map, name, {
          type: "FeatureCollection",
          features: index
            .filter(
              (f) =>
                f.east >= bounds.getWest() &&
                f.west <= bounds.getEast() &&
                f.north >= bounds.getSouth() &&
                f.south <= bounds.getNorth(),
            )
            .slice(0, 8000)
            .map((f) => f.feature),
        });
    };
    update();
    map.on("moveend", update);
    safeLayer(map, {
      id: "fallback-roads",
      type: "line",
      source: "fallback-roads",
      paint: { "line-color": dark ? "#7c826d" : "#faf7e9", "line-width": 3 },
    });
    safeLayer(map, {
      id: "fallback-buildings",
      type: "fill-extrusion",
      source: "fallback-buildings",
      paint: {
        "fill-extrusion-color": dark ? "#7b8471" : "#cfc8b1",
        "fill-extrusion-height": [
          "coalesce",
          ["get", "height_m"],
          ["*", ["coalesce", ["get", "levels"], 3], 3],
        ],
        "fill-extrusion-opacity": 0.95,
      },
    });
    return coast || buildings || roads
      ? "OSM · резервная карта · часть высот условная"
      : "Картографические данные недоступны";
  }
}

function viewGeometry(
  geo: Geo | null,
  floor?: number,
  facade?: string,
): GeoJSON.FeatureCollection {
  const features = (geo?.view_cones?.features || []).filter((feature) => {
    const p = feature.properties || {};
    return (
      (!facade || !p.facade || p.facade === facade) &&
      (!floor ||
        ((!p.floor || Number(p.floor) === floor) &&
          (!p.min_floor || floor >= Number(p.min_floor))))
    );
  });
  const fans: GeoJSON.Feature[] = [];
  const groups = new Map<
    string,
    {
      feature: GeoJSON.Feature;
      origin: GeoJSON.Position;
      end: GeoJSON.Position;
      angle: number;
    }[]
  >();
  for (const feature of features) {
    if (
      feature.geometry.type !== "LineString" ||
      feature.geometry.coordinates.length < 2
    )
      continue;
    const origin = feature.geometry.coordinates[0],
      end = feature.geometry.coordinates.at(-1)!;
    const angle =
      ((Math.atan2(
        (end[0] - origin[0]) * Math.cos((origin[1] * Math.PI) / 180),
        end[1] - origin[1],
      ) *
        180) /
        Math.PI +
        360) %
      360;
    const p = feature.properties || {},
      key = `${p.facade}|${p.floor}|${p.target}`;
    const group = groups.get(key) || [];
    group.push({ feature, origin, end, angle });
    groups.set(key, group);
  }
  for (const group of groups.values()) {
    group.sort((a, b) => a.angle - b.angle);
    for (let i = 0; i < group.length - 1; i++) {
      const a = group[i],
        b = group[i + 1];
      // Only interpolate between neighbouring calculated rays. Blocked gaps
      // remain empty instead of becoming an invented visible sector.
      if (
        b.angle - a.angle > 3 ||
        Math.abs(a.origin[0] - b.origin[0]) > 0.00001 ||
        Math.abs(a.origin[1] - b.origin[1]) > 0.00001
      )
        continue;
      fans.push({
        type: "Feature",
        properties: a.feature.properties,
        geometry: {
          type: "Polygon",
          coordinates: [[a.origin, a.end, b.end, a.origin]],
        },
      });
    }
  }
  return { type: "FeatureCollection", features: [...features, ...fans] };
}
function drawGeo(
  map: LibreMap,
  geo: Geo | null,
  layers: string[],
  floor?: number,
  facade?: string,
  month = 9,
  focusTrait?: string,
  landmarkId?: string,
  suppressLandmarkRays = false,
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
  const allCones = viewGeometry(geo, floor, facade);
  const target =
    focusTrait === "sea_view"
      ? ["sea"]
      : focusTrait === "mountain_view"
        ? ["mountain", "mountains"]
        : null;
  const cones =
    focusTrait === "landmark_view" || landmarkId
      ? empty
      : target
        ? {
            ...allCones,
            features: allCones.features.filter((f) =>
              target.includes(String(f.properties?.target)),
            ),
          }
        : allCones;
  const sun = geo?.sun_paths
    ?.find((s) => s.month === month)
    ?.points.filter((p) => p.elevation_deg >= 0)
    .at(-1);
  const savedSunsetAzimuth = geo?.facades
    ?.flatMap((f) => f.sunset_by_month || [])
    .find((s) => s.month === month)?.azimuth_deg;
  let sunset: GeoJSON.FeatureCollection = empty;
  if ((sun || savedSunsetAzimuth !== undefined) && geo?.center) {
    const [lon, lat] = geo.center,
      r = ((savedSunsetAzimuth ?? sun!.azimuth_deg) * Math.PI) / 180,
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
  const landmarkTargets = {
    type: "FeatureCollection" as const,
    features: (geo?.landmark_targets?.features || []).filter(
      (feature) =>
        !landmarkId || feature.properties?.landmark_id === landmarkId,
    ),
  };
  const landmarkRays = {
    type: "FeatureCollection" as const,
    features:
      suppressLandmarkRays || !floor
        ? []
        : (geo?.landmark_rays?.features || []).filter(
            (feature) =>
              feature.geometry.type === "LineString" &&
              feature.properties?.floor === floor &&
              (!facade || feature.properties?.facade === facade) &&
              (!landmarkId || feature.properties?.landmark_id === landmarkId),
          ),
  };
  // Mark where the walk ends so the route reads as «hotel → beach».
  const walk = (
    geo?.route as GeoJSON.FeatureCollection | undefined
  )?.features.find((f) => f.geometry.type === "LineString");
  const walkCoords =
    walk?.geometry.type === "LineString" ? walk.geometry.coordinates : [];
  const walkMinutes = Number(walk?.properties?.minutes);
  const routeEnd: GeoJSON.FeatureCollection = walkCoords.length
    ? {
        type: "FeatureCollection",
        features: [
          {
            type: "Feature",
            geometry: { type: "Point", coordinates: walkCoords.at(-1)! },
            properties: {
              label: Number.isFinite(walkMinutes)
                ? // Map glyphs cover Latin/Cyrillic only: no «≈» here.
                  `Пляж, ${roundedMinutes(walkMinutes)} мин пешком`
                : "Пляж",
            },
          },
        ],
      }
    : empty;
  for (const [id, data] of [
    ["selected-building", building],
    ["facades", facades],
    ["focused-facade", facade ? facades : empty],
    ["cones", cones],
    ["walk-route", geo?.route || empty],
    ["route-end", routeEnd],
    ["crossings", geo?.crossings || empty],
    ["sunset", sunset],
    ["landmark-targets", landmarkTargets],
    ["landmark-rays", landmarkRays],
  ] as const)
    put(map, id, data);
  safeLayer(map, {
    id: "landmark-contours-fill",
    type: "fill",
    source: "landmark-targets",
    filter: ["==", ["geometry-type"], "Polygon"],
    paint: { "fill-color": "#8e789b", "fill-opacity": 0.24 },
  });
  safeLayer(map, {
    id: "landmark-contours-line",
    type: "line",
    source: "landmark-targets",
    filter: ["==", ["geometry-type"], "Polygon"],
    paint: {
      "line-color": "#765684",
      "line-width": 3,
      "line-dasharray": [3, 2],
    },
  });
  safeLayer(map, {
    id: "landmark-rays-line",
    type: "line",
    source: "landmark-rays",
    paint: {
      "line-color": [
        "case",
        ["==", ["get", "available"], true],
        "#22775b",
        "#b85c4c",
      ],
      "line-width": 3,
      "line-dasharray": [4, 2],
    },
  });
  safeLayer(map, {
    id: "focused-facade-line",
    type: "line",
    source: "focused-facade",
    paint: {
      "line-color": "#fff6e9",
      "line-width": 11,
      "line-gap-width": 7,
      "line-opacity": 0.95,
    },
  });
  safeLayer(map, {
    id: "selected-building",
    type: "fill-extrusion",
    source: "selected-building",
    paint: {
      "fill-extrusion-color": "#f26143",
      "fill-extrusion-height": [
        "coalesce",
        ["get", "height_m"],
        ["get", "height"],
        9,
      ],
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
        ["mountains", "mountain"],
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
    filter: ["==", ["geometry-type"], "LineString"],
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
    id: "route-casing",
    type: "line",
    source: "walk-route",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": "#ffffff", "line-width": 10, "line-opacity": 0.9 },
  });
  safeLayer(map, {
    id: "route-line",
    type: "line",
    source: "walk-route",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": "#0e6b70", "line-width": 5 },
  });
  safeLayer(map, {
    id: "route-end-circle",
    type: "circle",
    source: "route-end",
    paint: {
      "circle-radius": 8,
      "circle-color": "#0e6b70",
      "circle-stroke-color": "#ffffff",
      "circle-stroke-width": 3,
    },
  });
  safeLayer(map, {
    id: "route-end-label",
    type: "symbol",
    source: "route-end",
    layout: {
      "text-field": ["get", "label"],
      "text-font": ["Noto Sans Medium"],
      "text-size": 13,
      "text-offset": [0, -1.6],
      "text-anchor": "bottom",
      "text-allow-overlap": true,
    },
    paint: {
      "text-color": "#0b4f53",
      "text-halo-color": "#ffffff",
      "text-halo-width": 2,
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
    route: [
      "route-casing",
      "route-line",
      "route-end-circle",
      "route-end-label",
      "crossings-circle",
    ],
    sun: ["sunset-line"],
    landmark: [
      "landmark-contours-fill",
      "landmark-contours-line",
      "landmark-rays-line",
    ],
  }))
    for (const id of ids)
      map.setLayoutProperty(
        id,
        "visibility",
        layers.includes(group) ? "visible" : "none",
      );
}
/** Demo zones, same boxes as pipeline/regions/sochi.yaml (lon/lat). */
const HERO_ZONES = [
  { name: "Центр Сочи", bbox: [39.69, 43.555, 39.79, 43.635] },
  { name: "Адлер — Имеретинка", bbox: [39.88, 43.38, 40.02, 43.46] },
] as const;
const HERO_BOUNDS: [[number, number], [number, number]] = [
  [39.66, 43.37],
  [40.04, 43.645],
];
function drawHeroOverlay(map: LibreMap, hotels: Hotel[]) {
  const inside = (h: Hotel, b: readonly number[]) =>
    h.lon >= b[0] && h.lon <= b[2] && h.lat >= b[1] && h.lat <= b[3];
  put(map, "hero-zones", {
    type: "FeatureCollection",
    features: HERO_ZONES.map((z) => {
      const [w, s, e, n] = z.bbox;
      return {
        type: "Feature",
        properties: {},
        geometry: {
          type: "Polygon",
          coordinates: [
            [
              [w, s],
              [e, s],
              [e, n],
              [w, n],
              [w, s],
            ],
          ],
        },
      };
    }),
  });
  put(map, "hero-zone-labels", {
    type: "FeatureCollection",
    features: HERO_ZONES.map((z) => ({
      type: "Feature",
      properties: {
        label: `${z.name}\n${hotels.filter((h) => inside(h, z.bbox)).length} отелей`,
      },
      geometry: { type: "Point", coordinates: [z.bbox[2], z.bbox[3]] },
    })),
  });
  put(map, "hero-sea", {
    type: "Feature",
    properties: { label: "Ч Ё Р Н О Е   М О Р Е" },
    geometry: { type: "Point", coordinates: [39.74, 43.45] },
  });
  safeLayer(map, {
    id: "hero-zones-fill",
    type: "fill",
    source: "hero-zones",
    paint: { "fill-color": "#e85c3b", "fill-opacity": 0.07 },
  });
  safeLayer(map, {
    id: "hero-zones-line",
    type: "line",
    source: "hero-zones",
    paint: {
      "line-color": "#e85c3b",
      "line-width": 1.5,
      "line-opacity": 0.75,
      "line-dasharray": [3, 2],
    },
  });
  safeLayer(map, {
    id: "hero-zone-labels",
    type: "symbol",
    source: "hero-zone-labels",
    layout: {
      "text-field": ["get", "label"],
      "text-font": ["Noto Sans Medium"],
      "text-size": 13,
      "text-anchor": "bottom-right",
      "text-offset": [0, -0.4],
      "text-justify": "right",
      "text-allow-overlap": true,
    },
    paint: {
      "text-color": "#b5452c",
      "text-halo-color": "#fbf9f3",
      "text-halo-width": 2,
    },
  });
  safeLayer(map, {
    id: "hero-sea",
    type: "symbol",
    source: "hero-sea",
    layout: {
      "text-field": ["get", "label"],
      "text-font": ["Noto Sans Regular"],
      "text-size": 15,
      "text-rotate": -32,
      "text-allow-overlap": true,
    },
    paint: { "text-color": "#42787b", "text-opacity": 0.85 },
  });
}
export function TravelMap({
  hotels = [],
  activeId,
  hoverId,
  onHover,
  onSelect,
  onOpen,
  geo: providedGeo,
  extraGeometry,
  hero = false,
  large = false,
  floor,
  facade,
  month = 9,
  focusTrait,
  landmarkId,
}: {
  hotels?: Hotel[];
  activeId?: string | null;
  hoverId?: string | null;
  onHover?: (id: string | null) => void;
  onSelect?: (id: string) => void;
  onOpen?: (id: string) => void;
  geo?: Geo | null;
  extraGeometry?: GeoJSON.Feature | GeoJSON.FeatureCollection | null;
  hero?: boolean;
  large?: boolean;
  floor?: number;
  facade?: string;
  month?: number;
  focusTrait?: string;
  landmarkId?: string;
}) {
  const { theme } = useApp();
  const container = useRef<HTMLDivElement>(null),
    mapRef = useRef<LibreMap | null>(null),
    markers = useRef<maplibregl.Marker[]>([]);
  const poiMarkers = useRef<maplibregl.Marker[]>([]);
  const poiPopup = useRef<maplibregl.Popup | null>(null);
  const facadeMarker = useRef<maplibregl.Marker | null>(null);
  const landmarkMarkers = useRef<maplibregl.Marker[]>([]);
  const [ready, setReady] = useState(false),
    [rendered, setRendered] = useState(false),
    [error, setError] = useState(""),
    [base, setBase] = useState("Загружаем локальную карту"),
    [layers, setLayers] = useState(["view", "noise", "route", "sun"]),
    [geo, setGeo] = useState<Geo | null>(providedGeo || null),
    [full, setFull] = useState(false);
  const hotelKey = hotels.map((h) => `${h.id}:${h.lon}:${h.lat}`).join("|");
  const hotelList = useRef(hotels);
  hotelList.current = hotels;
  const callbacks = useRef({ onHover, onSelect });
  callbacks.current = { onHover, onSelect };
  // Hover only highlights; the camera moves on an explicit click/selection.
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
    setError("");
    setRendered(false);
    setBase("Загружаем локальную карту");
    const dark = theme === "dark";
    let map: LibreMap;
    try {
      map = new maplibregl.Map({
        container: container.current,
        style: baseStyle(dark),
        center: initialCenter.current,
        zoom: hero ? 13.4 : 14.2,
        pitch: hero ? 40 : 48,
        bearing: hero ? -18 : -22,
        ...(hero
          ? {
              bounds: HERO_BOUNDS,
              fitBoundsOptions: {
                padding: { top: 90, bottom: 230, left: 170, right: 60 },
              },
            }
          : {}),
        attributionControl: false,
        maxPitch: 70,
        canvasContextAttributes: { antialias: true },
        // The page scrolls under the cursor; zoom needs Ctrl/⌘ + wheel.
        interactive: !hero,
        cooperativeGestures: !hero && !large,
        locale: {
          "CooperativeGesturesHandler.WindowsHelpText":
            "Чтобы приблизить карту, используйте Ctrl + колесо мыши",
          "CooperativeGesturesHandler.MacHelpText":
            "Чтобы приблизить карту, используйте ⌘ + колесо мыши",
          "CooperativeGesturesHandler.MobileHelpText":
            "Двигайте карту двумя пальцами",
        },
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
    map.on("idle", () => {
      if (
        !disposed &&
        (map.getSource("city") || map.getSource("fallback-buildings"))
      )
        setRendered(true);
    });
    map.on("load", async () => {
      try {
        const label = await attachBaseMap(map, dark, () => disposed);
        if (disposed) return;
        setBase(label);
        setRendered(false);
        setReady(true);
      } catch {
        if (!disposed)
          setError(
            "Не удалось загрузить локальную карту. Все факты доступны в списке.",
          );
      }
    });
    return () => {
      disposed = true;
      setReady(false);
      markers.current.forEach((m) => m.remove());
      markers.current = [];
      poiMarkers.current.forEach((m) => m.remove());
      poiMarkers.current = [];
      poiPopup.current?.remove();
      facadeMarker.current?.remove();
      facadeMarker.current = null;
      landmarkMarkers.current.forEach((marker) => marker.remove());
      landmarkMarkers.current = [];
      map.remove();
      mapRef.current = null;
    };
  }, [theme, hero, large]);
  useEffect(() => {
    if (providedGeo && !floor) {
      setGeo(providedGeo);
      return;
    }
    if (!activeId) {
      setGeo(null);
      return;
    }
    let active = true;
    setGeo(providedGeo || null);
    api
      .geo(activeId, { month, facade, floor })
      .then((g) => {
        if (active) setGeo(g);
      })
      .catch(() => {
        if (active) setGeo(providedGeo || null);
      });
    return () => {
      active = false;
    };
  }, [activeId, providedGeo, month, facade, floor]);
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    markers.current.forEach((m) => m.remove());
    markers.current = hotelList.current.map((hotel, i) => {
      // Overview dots are decorative; only interactive maps get buttons.
      const el = document.createElement(hero ? "span" : "button");
      if (el instanceof HTMLButtonElement) el.type = "button";
      el.className = "map-marker";
      el.dataset.hotelId = hotel.id;
      el.textContent = hero ? "" : String(i + 1);
      el.title = hotel.name;
      el.setAttribute("aria-label", hotel.name);
      el.dataset.testid = `map-hotel-${hotel.id}`;
      if (hero) {
        el.setAttribute("aria-hidden", "true");
        el.style.pointerEvents = "none";
      }
      el.onmouseenter = () => callbacks.current.onHover?.(hotel.id);
      el.onmouseleave = () => callbacks.current.onHover?.(null);
      el.onfocus = () => callbacks.current.onHover?.(hotel.id);
      el.onblur = () => callbacks.current.onHover?.(null);
      el.onclick = () => callbacks.current.onSelect?.(hotel.id);
      return new maplibregl.Marker({ element: el })
        .setLngLat([hotel.lon, hotel.lat])
        .addTo(map);
    });
  }, [hotelKey, ready, hero]);
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready || hero) return;
    const points = nearbyPoints(geo);
    poiMarkers.current.forEach((m) => m.remove());
    poiPopup.current?.remove();
    poiMarkers.current = points.map((feature) => {
      const properties = feature.properties || {};
      const kind = String(properties.kind);
      const entry = POI_KINDS[kind];
      const name =
        typeof properties.name === "string" && properties.name
          ? properties.name
          : entry.label;
      const el = document.createElement("button");
      el.type = "button";
      el.className = `map-poi-marker map-poi-${kind}${focusTrait && entry.trait.includes(focusTrait) ? " focused" : ""}`;
      el.textContent = entry.symbol;
      el.title = `${entry.label}: ${name}`;
      el.setAttribute("aria-label", el.title);
      el.dataset.testid = `map-poi-${kind}`;
      el.dataset.poiKind = kind;
      el.dataset.osmId = String(properties.osm_id || "");
      el.onclick = (event) => {
        // Do not let the opening click immediately trigger Popup.closeOnClick.
        event.stopPropagation();
        poiPopup.current?.remove();
        const content = document.createElement("div");
        content.className = "map-poi-popup";
        content.dataset.testid = "map-poi-popup";
        const title = document.createElement("strong");
        title.textContent = name;
        content.append(title);
        const detail = document.createElement("p");
        detail.textContent = `${entry.label} · ${properties.coordinate_source === "route_endpoint" ? "конец рассчитанного маршрута" : "картированный объект OSM"}${typeof properties.distance_m === "number" ? ` · ${meters(properties.distance_m)} по прямой` : ""}`;
        content.append(detail);
        const caveat = document.createElement("p");
        caveat.textContent =
          "Метка не подтверждает вход, часы работы и доступность объекта.";
        content.append(caveat);
        poiPopup.current = new maplibregl.Popup({
          offset: 18,
          closeButton: true,
          maxWidth: "260px",
        })
          .setLngLat(feature.geometry.coordinates as [number, number])
          .setDOMContent(content)
          .addTo(map);
      };
      return new maplibregl.Marker({ element: el })
        .setLngLat(feature.geometry.coordinates as [number, number])
        .addTo(map);
    });
    return () => {
      poiMarkers.current.forEach((m) => m.remove());
      poiMarkers.current = [];
      poiPopup.current?.remove();
    };
  }, [geo, ready, hero, focusTrait]);
  useEffect(() => {
    landmarkMarkers.current.forEach((marker) => marker.remove());
    landmarkMarkers.current = [];
    const map = mapRef.current;
    if (!map || !ready || hero || !layers.includes("landmark")) return;
    landmarkMarkers.current = (geo?.landmark_targets?.features || [])
      .filter(
        (feature): feature is GeoJSON.Feature<GeoJSON.Point> =>
          feature.geometry.type === "Point" &&
          (!landmarkId || feature.properties?.landmark_id === landmarkId),
      )
      .map((feature) => {
        const properties = feature.properties || {};
        const id = String(properties.landmark_id || "");
        const element = document.createElement("button");
        element.type = "button";
        element.className = "map-landmark-marker";
        element.textContent = "Д";
        element.title = String(properties.name || landmarkLabel(id));
        element.setAttribute("aria-label", `Цель расчёта: ${element.title}`);
        element.dataset.testid = `map-landmark-${id}`;
        element.onclick = (event) => {
          event.stopPropagation();
          poiPopup.current?.remove();
          const content = document.createElement("div");
          content.className = "map-poi-popup";
          content.dataset.testid = "landmark-popup";
          const title = document.createElement("strong");
          title.textContent = element.title;
          const description = document.createElement("p");
          description.textContent = `${landmarkHeightSource(properties.height_source)}. Проверяется верх моделируемого контура.`;
          const proof = document.createElement("details");
          const summary = document.createElement("summary");
          summary.textContent = "Для дотошных";
          const source = document.createElement("p");
          source.textContent = `Источник высоты: ${properties.height_source || "неизвестен"}`;
          proof.append(summary, source);
          content.append(title, description, proof);
          poiPopup.current = new maplibregl.Popup({
            offset: 18,
            maxWidth: "280px",
          })
            .setLngLat(feature.geometry.coordinates as [number, number])
            .setDOMContent(content)
            .addTo(map);
        };
        return new maplibregl.Marker({ element })
          .setLngLat(feature.geometry.coordinates as [number, number])
          .addTo(map);
      });
    return () => {
      landmarkMarkers.current.forEach((marker) => marker.remove());
      landmarkMarkers.current = [];
    };
  }, [geo, ready, hero, layers, landmarkId]);
  useEffect(() => {
    facadeMarker.current?.remove();
    const map = mapRef.current;
    if (!map || !ready || hero || focusTrait !== "room_orientation" || !facade)
      return;
    const geometry = geo?.facades?.find(
      (entry) => entry.code === facade,
    )?.geometry;
    const coordinates =
      geometry?.type === "LineString"
        ? geometry.coordinates
        : geometry?.type === "MultiLineString"
          ? geometry.coordinates[0]
          : null;
    if (!coordinates || coordinates.length < 2) return;
    const i = Math.floor((coordinates.length - 1) / 2);
    const a = coordinates[i],
      b = coordinates[i + 1];
    if (![a[0], a[1], b[0], b[1]].every(Number.isFinite)) return;
    const label = document.createElement("span");
    label.className = "map-facade-focus";
    label.dataset.testid = "map-facade-focus";
    label.textContent = `Фасад: ${SIDES[facade] || facade}`;
    // Position comes from the selected facade segment, never a guessed window.
    facadeMarker.current = new maplibregl.Marker({
      element: label,
      offset: [0, -24],
    })
      .setLngLat([(a[0] + b[0]) / 2, (a[1] + b[1]) / 2])
      .addTo(map);
    return () => {
      facadeMarker.current?.remove();
      facadeMarker.current = null;
    };
  }, [geo, ready, hero, facade, focusTrait]);
  useEffect(() => {
    if (!focusTrait) return;
    const layer = ["quiet_night", "railway_noise"].includes(focusTrait)
      ? "noise"
      : focusTrait === "landmark_view" || landmarkId
        ? "landmark"
        : ["sea_view", "mountain_view"].includes(focusTrait)
          ? "view"
          : ["beach_walk", "accessible_route", "beach_surface"].includes(
                focusTrait,
              )
            ? "route"
            : focusTrait === "sunset_view"
              ? "sun"
              : null;
    if (layer)
      setLayers((old) => (old.includes(layer) ? old : [...old, layer]));
    const map = mapRef.current;
    if (!map || !ready || !geo?.center) return;
    if (layer === "landmark") {
      const targets =
        geo.landmark_targets?.features.filter(
          (feature) =>
            !landmarkId || feature.properties?.landmark_id === landmarkId,
        ) || [];
      if (!targets.length) return;
      const bounds = new maplibregl.LngLatBounds(geo.center, geo.center);
      const extend = (coordinates: unknown) => {
        if (!Array.isArray(coordinates)) return;
        if (
          typeof coordinates[0] === "number" &&
          typeof coordinates[1] === "number"
        )
          bounds.extend(coordinates as [number, number]);
        else coordinates.forEach(extend);
      };
      targets.forEach((feature) => {
        if ("coordinates" in feature.geometry)
          extend(feature.geometry.coordinates);
      });
      map.fitBounds(bounds, {
        padding: { top: 105, bottom: 100, left: 65, right: 65 },
        maxZoom: 16.2,
        duration: 650,
      });
      return;
    }
    const points = nearbyPoints(geo).filter((f) =>
      POI_KINDS[String(f.properties?.kind)].trait.includes(focusTrait),
    );
    if (!points.length) return;
    const bounds = new maplibregl.LngLatBounds(geo.center, geo.center);
    points.forEach((f) =>
      bounds.extend(f.geometry.coordinates as [number, number]),
    );
    map.fitBounds(bounds, {
      padding: { top: 105, bottom: 100, left: 65, right: 65 },
      maxZoom: 16.2,
      duration: 650,
    });
  }, [focusTrait, landmarkId, geo, ready]);
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready || !hero) return;
    drawHeroOverlay(map, hotelList.current);
  }, [ready, hero, hotelKey]);
  useEffect(() => {
    markers.current.forEach((marker) => {
      const el = marker.getElement();
      el.classList.toggle("selected", el.dataset.hotelId === activeId);
      el.classList.toggle("hovered", el.dataset.hotelId === hoverId);
    });
  }, [activeId, hoverId, ready, hotelKey]);
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    drawGeo(
      map,
      geo,
      layers,
      floor,
      facade,
      month,
      focusTrait,
      landmarkId,
      !!extraGeometry,
    );
    put(map, "scenario-building", extraGeometry || empty);
    safeLayer(map, {
      id: "scenario-building",
      type: "fill-extrusion",
      source: "scenario-building",
      paint: {
        "fill-extrusion-color": "#c84f3a",
        "fill-extrusion-height": [
          "coalesce",
          ["get", "height_m"],
          ["get", "height"],
          0,
        ],
        "fill-extrusion-opacity": 0.9,
      },
    });
  }, [
    geo,
    layers,
    ready,
    floor,
    facade,
    month,
    extraGeometry,
    focusTrait,
    landmarkId,
  ]);
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready || hero) return;
    const route = (
      geo?.route as GeoJSON.FeatureCollection | undefined
    )?.features.flatMap((f) =>
      f.geometry.type === "LineString" ? f.geometry.coordinates : [],
    );
    if (geo?.center && route?.length && !hero) {
      // Frame the whole walk to the beach, not just the building.
      const b = new maplibregl.LngLatBounds(geo.center, geo.center);
      route.forEach((p) => b.extend(p as [number, number]));
      map.fitBounds(b, {
        padding: { top: 90, bottom: 110, left: 70, right: 70 },
        maxZoom: 16.2,
        duration: 900,
      });
    } else if (geo?.center)
      map.easeTo({ center: geo.center, zoom: 15.8, duration: 900 });
    else if (hotels.length) {
      const b = new maplibregl.LngLatBounds();
      hotels.forEach((h) => b.extend([h.lon, h.lat]));
      map.fitBounds(b, { padding: 80, maxZoom: 14.5, duration: 800 });
    }
  }, [geo?.hotel_id, geo?.center?.[0], geo?.center?.[1], ready, hotelKey]);
  const selected = hotels.find((h) => h.id === (hoverId || activeId));
  const selectedTargets = (geo?.landmark_targets?.features || []).filter(
    (feature) => !landmarkId || feature.properties?.landmark_id === landmarkId,
  );
  const selectedRays =
    !floor || extraGeometry
      ? []
      : (geo?.landmark_rays?.features || []).filter(
          (feature) =>
            feature.properties?.floor === floor &&
            (!facade || feature.properties?.facade === facade) &&
            (!landmarkId || feature.properties?.landmark_id === landmarkId),
        );
  return (
    <div
      className={`travel-map ${hero ? "hero-map" : ""} ${large ? "large-map" : ""}`}
      data-testid="travel-map"
      data-map-status={
        error ? "error" : ready && rendered ? "ready" : "loading"
      }
      data-map-source={base}
      data-selected-trait={focusTrait}
      data-selected-facade={facade}
      data-selected-floor={floor}
      data-selected-month={month}
      data-selected-landmark={landmarkId}
      data-landmark-target-count={selectedTargets.length}
      data-landmark-ray-count={selectedRays.length}
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
              ...(geo?.landmark_targets?.features.length
                ? [["landmark", "Объекты"]]
                : []),
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
          {layers.includes("noise") && (
            <div
              className="map-noise-legend"
              title="Цвет фасада по верхней границе приближённого диапазона, не измерение в номере"
            >
              <span>Шум ночью, по расчёту</span>
              <div>
                <i />
                тише <i />
                умеренно <i />
                шумнее
              </div>
            </div>
          )}
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
              <span className="eyebrow">
                {hoverId && hoverId !== activeId ? "Под курсором" : "В фокусе"}
              </span>
              <strong>{selected.name}</strong>
              <span className="map-selected-note">
                {selected.id !== activeId
                  ? "Нажмите на метку, чтобы показать расчёт на карте"
                  : "Выберите условие, чтобы изучить расчёт"}
              </span>
              {onOpen && (
                <button
                  className="text-button map-open"
                  onClick={() => onOpen(selected.id)}
                >
                  Открыть отель <ArrowRight size={14} />
                </button>
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
          {nearbyPoints(geo).length > 0 && (
            <div
              className="map-nearby-legend"
              aria-label="Объекты рядом"
              data-testid="map-nearby-legend"
            >
              {[
                ...new Set(
                  nearbyPoints(geo).map((f) => String(f.properties?.kind)),
                ),
              ].map((kind) => (
                <span key={kind}>
                  <i className={`map-poi-key map-poi-${kind}`}>
                    {POI_KINDS[kind].symbol}
                  </i>
                  {POI_KINDS[kind].label}
                </span>
              ))}
            </div>
          )}
          {layers.includes("landmark") &&
            (selectedTargets.length > 0 ||
              landmarkId ||
              focusTrait === "landmark_view") && (
              <LandmarkMapProof
                targets={selectedTargets}
                rays={selectedRays}
                metadata={geo?.landmark_metadata}
                warnings={geo?.warnings}
                landmarkId={landmarkId}
                floor={floor}
                scenario={!!extraGeometry}
              />
            )}
        </>
      )}
      <div className="map-source">
        <Layers3 size={11} />
        Схема района, не фото
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
            hoverId={hoverId}
            geo={geo}
            extraGeometry={extraGeometry}
            large
            onHover={onHover}
            onSelect={onSelect}
            onOpen={onOpen}
            month={month}
            floor={floor}
            facade={facade}
            focusTrait={focusTrait}
            landmarkId={landmarkId}
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
  const count = (value: unknown) =>
    typeof value === "number" && Number.isInteger(value) && value >= 1
      ? value
      : null;
  const knownFloors = count(hotel.floors);
  const maximum = knownFloors
    ? Math.min(
        knownFloors,
        count(geo.floors?.count) || knownFloors,
        count(geo.floors?.modeled_count) || knownFloors,
        100,
      )
    : 1;
  const [requestedFloor, setRequestedFloor] = useState(maximum),
    [facade, setFacade] = useState(geo.facades?.[0]?.code || ""),
    [month, setMonth] = useState(9);
  const floor = Math.max(1, Math.min(maximum, Math.trunc(requestedFloor) || 1));
  useEffect(() => {
    setRequestedFloor((current) =>
      Math.max(1, Math.min(maximum, Math.trunc(current) || 1)),
    );
  }, [maximum]);
  useEffect(() => {
    if (!geo.facades?.some((entry) => entry.code === facade))
      setFacade(geo.facades?.[0]?.code || "");
  }, [geo.facades, facade]);
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
  const selectedFacade = geo.facades?.find((f) => f.code === facade);
  const probability = selectedFacade?.sea_probability_by_floor
    ? selectedFacade.sea_probability_by_floor[floor - 1]
    : mc?.probability;
  const p80 = selectedFacade?.min_floor_sea_p80;
  const sunset = selectedFacade?.sunset_by_month?.find(
    (s) => s.month === month,
  );
  const sunsetMinutes = sunset?.visible_minutes[floor - 1];
  const solarDate = geo.sun_paths?.find((s) => s.month === month)?.date;
  const availableRayFloors = [
    ...new Set(
      geo.view_cones?.features
        .filter((f) => !facade || f.properties?.facade === facade)
        .map((f) => Number(f.properties?.floor))
        .filter(Number.isFinite) || [],
    ),
  ];

  return (
    <Modal wide title="От карты — к вашему окну" onClose={onClose}>
      <p className="muted">
        {hotel.name} · геометрия, положение солнца и границы достоверности
      </p>
      <p className="fine-print" data-testid="geo-floor-note">
        {knownFloors
          ? `Известная этажность: ${knownFloors}. В сохранённой модели доступны ${maximum} уровней; наличие номеров не подтверждается.`
          : "Этажность отеля неизвестна. Показан только условный первый уровень выбранного контура; это не подтверждённый этаж или номер отеля."}
      </p>
      <div className="geo-controls">
        <label>
          Фасад
          <select
            aria-label="Фасад"
            data-testid="geo-facade"
            value={facade}
            onChange={(e) => setFacade(e.target.value)}
          >
            {geo.facades?.map((f) => (
              <option key={f.code} value={f.code}>
                {SIDES[f.code] || f.code}
              </option>
            ))}
            {!geo.facades?.length && <option value="">Нет данных</option>}
          </select>
        </label>
        <label>
          {knownFloors ? "Этаж" : "Этаж (условно)"}
          <input
            type="number"
            aria-label={knownFloors ? "Этаж" : "Этаж (условно)"}
            data-testid="geo-floor"
            min="1"
            max={maximum}
            disabled={!knownFloors}
            value={floor}
            onChange={(e) =>
              setRequestedFloor(
                Math.max(
                  1,
                  Math.min(maximum, Math.trunc(Number(e.target.value)) || 1),
                ),
              )
            }
          />
        </label>
        <label>
          Месяц
          <select
            aria-label="Месяц расчёта"
            data-testid="geo-month"
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
          <h3>Вид по расчёту</h3>
          {probability !== undefined && probability !== null ? (
            <details className="room-caveats-details">
              <summary>Для дотошных</summary>
              <div className="probability">
                {Math.round(probability * 100)}
                <span>%</span>
              </div>
              <p>
                Видимость в {mc?.iterations || "неизвестном числе"} симуляциях
                неопределённых входных высот.
              </p>
              {knownFloors && p80 && p80 <= maximum && (
                <p>
                  80% устойчивости: <strong>с {p80}-го этажа</strong>
                </p>
              )}
            </details>
          ) : (
            <Empty title="Вероятность не рассчитана">
              Для оценки не хватает данных о высоте зданий и рельефе.
            </Empty>
          )}
          {sunsetMinutes !== undefined && sunsetMinutes !== null && (
            <p className="sunset-reading">
              <strong>
                Закат:{" "}
                {sunsetMinutes > 0
                  ? `${sunsetMinutes} мин видимости`
                  : "не виден"}
              </strong>
              <br />
              {SIDES[facade] || "Сторона не выбрана"} ·{" "}
              {knownFloors ? `${floor}-й этаж` : `${floor}-й этаж (условно)`} ·
              опорная дата {sunset?.date || solarDate || "не передана"}
            </p>
          )}
          <p className="fine-print">
            Показан сохранённый расчёт отеля. Выбор фасада и этажа фильтрует
            доступные слои и рассчитанные значения. Новый расчёт не запускается.
          </p>
        </section>
      </div>
      {availableRayFloors.length > 0 && !availableRayFloors.includes(floor) && (
        <p className="fine-print">
          Лучи обзора сохранены для этажей: {availableRayFloors.join(", ")}. Для
          выбранного этажа геометрия лучей отсутствует; вероятность показана
          только при наличии расчёта.
        </p>
      )}
      <section className="horizon-section">
        <div className="section-heading">
          <div>
            <span className="eyebrow">Что видно из окна</span>
            <h3>Что закрывает горизонт</h3>
          </div>
          <div className="graph-legend">
            <span>● Рельеф</span>
            <span>● Здания</span>
            <span>● Солнце</span>
            <span>● Море</span>
          </div>
        </div>
        <p className="fine-print">
          Сохранённый профиль горизонта.{" "}
          {solarDate
            ? `Солнечный путь рассчитан на ${solarDate}; на точные даты поездки пересчёт не выполнялся.`
            : "Опорная дата солнечного пути не передана."}
        </p>
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
                  {
                    (
                      {
                        0: "Север",
                        90: "Восток",
                        180: "Юг",
                        270: "Запад",
                        360: "Север",
                      } as Record<number, string>
                    )[a]
                  }
                </text>
              </g>
            ))}
            <g transform="translate(20 0)">
              {horizon
                .filter((h) => h.sea)
                .map((h) => (
                  <line
                    key={h.azimuth_deg}
                    x1={h.azimuth_deg * 2}
                    x2={h.azimuth_deg * 2 + 5}
                    y1={180}
                    y2={180}
                    stroke="#6b9d9e"
                    strokeWidth={5}
                  />
                ))}
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
      <details className="limitations">
        <summary>Для дотошных</summary>
        {geo.limitations?.length ? (
          geo.limitations.map((l) => <p key={l}>{guestText(l)}</p>)
        ) : (
          <p>
            API не передал ограничения расчёта. До подтверждения отелем вид из
            конкретного номера не гарантирован.
          </p>
        )}
        <p>
          Наружный шум рассчитан приближённо и не описывает шумоизоляцию окон.
        </p>
      </details>
    </Modal>
  );
}
/**
 * Clean oblique 3D view of one hotel building, used offline by
 * scripts/render_building_snapshots.mjs as the card image for hotels
 * without a photo. It is a model of open data, not a photograph.
 */
function grow(geometry: GeoJSON.Geometry | null): GeoJSON.Geometry | null {
  if (!geometry || geometry.type !== "Polygon") return geometry;
  const ring = geometry.coordinates[0];
  const xs = ring.map((p) => p[0]),
    ys = ring.map((p) => p[1]);
  const cx = (Math.min(...xs) + Math.max(...xs)) / 2,
    cy = (Math.min(...ys) + Math.max(...ys)) / 2;
  const metres = Math.max(
    (Math.max(...xs) - Math.min(...xs)) *
      111320 *
      Math.cos((cy * Math.PI) / 180),
    (Math.max(...ys) - Math.min(...ys)) * 110540,
    4,
  );
  const k = 1 + 2.4 / metres;
  return {
    type: "Polygon",
    coordinates: geometry.coordinates.map((r) =>
      r.map(([x, y]) => [cx + (x - cx) * k, cy + (y - cy) * k]),
    ),
  };
}
function snapshotZoom(geometry: GeoJSON.Geometry | null) {
  if (!geometry || geometry.type !== "Polygon") return 17.2;
  const ring = geometry.coordinates[0];
  const span = Math.max(
    Math.max(...ring.map((p) => p[0])) - Math.min(...ring.map((p) => p[0])),
    Math.max(...ring.map((p) => p[1])) - Math.min(...ring.map((p) => p[1])),
  );
  // ~20 m buildings get a closer camera than ~150 m resort blocks.
  return span < 0.0003 ? 17.5 : span < 0.0008 ? 17.1 : 16.6;
}
export function BuildingSnapshot({ hotel }: { hotel: Hotel }) {
  const container = useRef<HTMLDivElement>(null);
  const [status, setStatus] = useState("loading");
  useEffect(() => {
    if (!container.current) return;
    if (!registered) {
      maplibregl.addProtocol("pmtiles", protocol.tile);
      registered = true;
    }
    let disposed = false;
    const map = new maplibregl.Map({
      container: container.current,
      style: baseStyle(false),
      center: [hotel.lon, hotel.lat],
      zoom: snapshotZoom(hotel.footprint as GeoJSON.Geometry | null),
      pitch: 56,
      bearing: -28,
      interactive: false,
      attributionControl: false,
      canvasContextAttributes: { antialias: true, preserveDrawingBuffer: true },
    });
    map.on("load", async () => {
      try {
        await attachBaseMap(map, false, () => disposed);
      } catch {
        if (!disposed) setStatus("error");
        return;
      }
      if (disposed) return;
      const height = (hotel.floors || 4) * 3.2 + 1.5;
      // Grow the outline ~1 m so its walls cover the basemap building
      // instead of z-fighting with it.
      const footprint = grow(hotel.footprint as GeoJSON.Geometry | null);
      put(
        map,
        "snapshot-building",
        footprint
          ? {
              type: "Feature",
              geometry: footprint,
              properties: { height_m: height },
            }
          : empty,
      );
      safeLayer(map, {
        id: "snapshot-building",
        type: "fill-extrusion",
        source: "snapshot-building",
        paint: {
          "fill-extrusion-color": "#e85c3b",
          "fill-extrusion-height": ["get", "height_m"],
          "fill-extrusion-opacity": 0.96,
        },
      });
      put(map, "snapshot-point", {
        type: "Feature",
        geometry: { type: "Point", coordinates: [hotel.lon, hotel.lat] },
        properties: {},
      });
      // A pin keeps the hotel findable when taller neighbours hide it.
      const el = document.createElement("div");
      el.className = "snapshot-pin";
      new maplibregl.Marker({ element: el, anchor: "bottom", offset: [0, -26] })
        .setLngLat([hotel.lon, hotel.lat])
        .addTo(map);
      map.once("idle", () => {
        if (!disposed) setStatus("ready");
      });
    });
    return () => {
      disposed = true;
      map.remove();
    };
  }, [hotel.id, hotel.lon, hotel.lat, hotel.floors, hotel.footprint]);
  return (
    <div
      className="snapshot-map"
      ref={container}
      data-snapshot-status={status}
      data-testid="building-snapshot"
    />
  );
}
