export type Json = Record<string, unknown>;
export type Mode = "offline" | "cache" | "live";
export type Side = "N" | "NE" | "E" | "SE" | "S" | "SW" | "W" | "NW";
export interface Scope {
  type: "hotel" | "facade" | "floor_range" | "room_type";
  facade?: Side | null;
  floor_min?: number | null;
  floor_max?: number | null;
  room_type?: string | null;
  month?: number | null;
}
export interface Evidence {
  id: string;
  kind:
    | "calculation"
    | "open_data"
    | "quote"
    | "hotelier"
    | "simulation"
    | "mock";
  label: string;
  source_ids: string[];
  url?: string | null;
  artifact?: string | null;
  quote?: string | null;
  message_id?: string | null;
  inputs: Json;
  output: Json;
  limitations: string[];
}
export interface Claim {
  id: string;
  hotel_id: string;
  characteristic_id: string;
  scope: Scope;
  value: unknown;
  source: string;
  method: string;
  evidence: Evidence[];
  confidence: number;
  observed_at: string;
  valid_until: string;
  synthetic: boolean;
}
export interface Fact {
  id: string;
  hotel_id: string;
  characteristic_id: string;
  scope: Scope;
  value: unknown;
  confidence: number;
  status: "computed" | "confirmed" | "disputed" | "unknown" | "stale";
  resolution: string;
  supporting_claim_ids: string[];
  conflicting_claim_ids: string[];
  evidence: Evidence[];
  updated_at: string;
  valid_until: string | null;
  synthetic: boolean;
}
export interface Hotel {
  id: string;
  name: string;
  lat: number;
  lon: number;
  address: string;
  stars: number | null;
  price_rub: number | null;
  basic_fields_source: string;
  source_ids: string[];
  floors: number | null;
  footprint: Json | null;
  image: string | null;
  image_attribution: string | null;
  website: string | null;
  room_types: Json[];
  tags: Json;
}
export interface Criterion {
  characteristic_id: string;
  label: string;
  op: "eq" | "lte" | "gte" | "contains";
  value: unknown;
  field?: string | null;
  weight: number;
  scope?: Scope | null;
}
export interface ParsedQuery {
  text: string;
  must: Criterion[];
  nice: Criterion[];
  check_in: string | null;
  check_out: string | null;
  month: number;
  persona?: string | null;
  min_floor?: number | null;
  facade?: Side | null;
  unmapped: string[];
  clarifications: string[];
  mode: Mode;
}
export interface Verdict {
  characteristic_id: string;
  label: string;
  required: boolean;
  status: "yes" | "partial" | "no" | "unknown";
  symbol: string;
  text: string;
  confidence: number;
  fact_ids: string[];
  evidence_refs: string[];
  caveat?: string | null;
  synthetic?: boolean;
  disputed?: boolean;
}
export interface SearchHit {
  hotel: Hotel;
  score: number;
  group: "all" | "almost" | "unknown";
  must_satisfied: number;
  must_total: number;
  verdicts: Verdict[];
  room_recommendation: string | null;
  disappointments: string[];
  summary: string;
}
export interface SearchResult {
  parsed: ParsedQuery;
  results: SearchHit[];
  total: number;
  latency_ms: number;
  run_id: string | null;
  mode: Mode;
}
export interface SearchRequest {
  text: string;
  check_in?: string | null;
  check_out?: string | null;
  parsed?: ParsedQuery | null;
  similar_to?: string | null;
  limit?: number;
}
export interface Question {
  id: string;
  hotel_id: string;
  characteristic_ids: string[];
  text: string;
  hypothesis: string | null;
  options: string[];
  priority: number;
  reason: string;
  status: string;
  demand_estimate: number;
  demand_synthetic: boolean;
  asked_at: string;
}
export interface Conflict {
  id: string;
  characteristic_id: string;
  resolution_note: string;
  kind: string;
  status: string;
  synthetic: boolean;
}
export interface HotelDetail {
  hotel: Hotel;
  facts: Fact[];
  claims: Claim[];
  conflicts: Conflict[];
  questions: Question[];
  verdicts: Verdict[];
  room_recommendation: string;
  disappointments: string[];
  geo: Geo;
}
export interface FieldUpdate {
  characteristic_id: string;
  scope: Scope;
  value: unknown;
  quote: string;
  confidence: number;
}
export interface Answer {
  id: string;
  question_id: string;
  raw_text: string;
  updates: FieldUpdate[];
  unknown_fields: string[];
  needs_clarification: string | null;
  parser: Mode;
  confirmed: boolean;
  synthetic: boolean;
  created_claim_ids: string[];
}
export interface PassportItem {
  id: string;
  characteristic_id: string;
  text: string;
  critical: boolean;
  scope: Scope;
  fact_ids: string[];
  status: string;
  response: string | null;
}
export interface Passport {
  id: string;
  hotel_id: string;
  guest_name: string;
  check_in: string | null;
  check_out: string | null;
  items: PassportItem[];
  status: string;
  room_number: string | null;
  timeline: Json[];
  notifications: Json[];
  synthetic_booking: boolean;
  created_at: string;
}
export interface PassportCreate {
  hotel_id: string;
  guest_name: string;
  check_in?: string | null;
  check_out?: string | null;
  items: PassportItem[];
  parsed?: ParsedQuery | null;
}
export interface ToolCall {
  tool: string;
  params: Json;
  rationale: string;
  when?: string | null;
}
export interface Spec {
  id: string;
  version: number;
  name_ru: string;
  description: string;
  category: string;
  synonyms: string[];
  scope: Scope["type"];
  value_schema: Json;
  methods: ToolCall[];
  ttl_days: number;
  search: {
    kind: string;
    field: string;
    default_op: Criterion["op"];
    default_value: unknown;
    explain_ok: string;
    explain_bad: string;
  };
  hotelier_answerable: boolean;
  status: string;
}
export interface CompileResult {
  spec: Spec;
  yaml_text: string;
  plan: ToolCall[];
  warnings: string[];
  mode: Mode;
}
export interface DryResult {
  spec_id: string;
  results: { hotel_id: string; claims: Claim[]; warnings: string[] }[];
  run_id: string;
  valid: boolean;
}
export interface PublishResult {
  spec: Spec;
  run_id: string;
  processed: number;
  total: number;
  status: string;
}
export interface AgentStep {
  id: string;
  run_id: string;
  seq: number;
  agent: string;
  type: string;
  name: string;
  input: Json;
  output: Json;
  rationale: string;
  evidence_refs: string[];
  duration_ms: number;
  mode: Mode;
  cache_hit: boolean;
  error: string | null;
  created_at: string;
}
export interface AgentRun {
  id: string;
  kind: string;
  hotel_id: string | null;
  status: string;
  llm_mode: Mode;
  steps: AgentStep[];
  started_at: string;
  finished_at: string | null;
  error: string | null;
}
export interface ChangeEvent {
  id: string;
  kind: string;
  title: string;
  geometry: Json | null;
  payload: Json;
  affected_hotel_ids: string[];
  before: Fact[];
  after: Fact[];
  notifications: Json[];
  synthetic: boolean;
  detected_at: string;
}
export interface Source {
  id: string;
  name: string;
  kind: string;
  url: string;
  license: string;
  attribution: string;
  observed_at: string;
  retrieved_at: string;
  used: boolean;
  notes: string;
}
export interface Status {
  name: string;
  mode: Mode;
  version: string;
  hotel_count: number;
  characteristic_count: number;
  seed_version: string;
}
export interface HotelierData {
  hotel: Hotel;
  facts: Fact[];
  claims: Claim[];
  questions: Question[];
  conflicts: Conflict[];
  passports: Passport[];
  strengths: unknown[];
  demand_synthetic: boolean;
}
export interface LabelTarget {
  hotel: Hotel;
  facts: Fact[];
  evidence_links: unknown[];
  existing_labels: unknown[];
}
export interface Geo {
  hotel_id?: string;
  center?: [number, number];
  building?: GeoJSON.Feature;
  computed_at?: string;
  artifact?: string;
  floors?: { count?: number | null; modeled_count?: number };
  facades?: {
    code: string;
    azimuth_deg: number;
    geometry: GeoJSON.Geometry;
    min_floor_sea?: number | null;
    min_floor_sea_p80?: number | null;
    sea_probability_by_floor?: (number | null)[];
    sea_width_by_floor?: (number | null)[];
    sunset_by_month?: {
      month: number;
      date?: string;
      azimuth_deg?: number;
      available: (boolean | null)[];
      visible_minutes: (number | null)[];
    }[];
    min_floor_sunset?: number | null;
    night_db_min?: number | null;
    night_db_max?: number | null;
    confidence?: number;
  }[];
  horizon?: {
    azimuth_deg: number;
    terrain_deg: number | null;
    buildings_deg: number | null;
    sea: boolean;
  }[];
  sun_paths?: {
    month: number;
    date?: string;
    points: { azimuth_deg: number; elevation_deg: number }[];
  }[];
  view_cones?: GeoJSON.FeatureCollection;
  route?: GeoJSON.FeatureCollection;
  crossings?: GeoJSON.FeatureCollection;
  monte_carlo?: {
    iterations?: number;
    probability?: number | null;
    min_floor_p80?: number | null;
  };
  limitations?: string[];
}
