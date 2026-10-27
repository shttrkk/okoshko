export type Json = Record<string, unknown>;
export type Mode = "offline" | "cache" | "live";
export type Side = "N" | "NE" | "E" | "SE" | "S" | "SW" | "W" | "NW";
export interface HotelPhoto {
  kind: "building" | "official_site" | "street" | "room";
  url: string;
  local: boolean;
  page_url?: string | null;
  title?: string | null;
  author?: string | null;
  license?: string | null;
  license_url?: string | null;
  match?: string | null;
  match_text?: string | null;
  distance_m?: number | null;
  width?: number;
  height?: number;
  bytes?: number | null;
  sha256?: string | null;
  verified_by_eye?: boolean;
  captured_year?: number | null;
  captured_at?: string | null;
  image_id?: string | null;
}
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
  resolution?: string;
  supporting_claim_ids: string[];
  conflicting_claim_ids?: string[];
  evidence: Evidence[];
  updated_at: string;
  valid_until?: string | null;
  synthetic?: boolean;
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
  no_stairs?: boolean;
  no_crossings?: boolean;
  weight: number;
  scope?: Scope | null;
}
export interface RoomScope {
  facade?: Side | null;
  floor_min?: number | null;
  floor_max?: number | null;
  room_type?: string | null;
  month?: number | null;
}
export interface RoomRequest {
  label: string;
  must: Criterion[];
  nice: Criterion[];
  scope: RoomScope;
  no_data?: Criterion[];
  no_data_must?: Criterion[];
  unmapped?: string[];
  clarifications?: string[];
}
export interface RoomClause {
  text: string;
  characteristic_id?: string | null;
  fact_ids: string[];
  evidence_refs: string[];
  status: "yes" | "partial" | "no" | "unknown";
  synthetic: boolean;
}
export interface RoomProfile {
  id: string;
  scope: RoomScope;
  title: string;
  description: string;
  clauses: RoomClause[];
  caveats: string[];
  conditional: boolean;
}
export interface RoomPlanItem {
  label: string;
  scope: RoomScope;
  recommendation: string;
  verdicts: Verdict[];
  status: "yes" | "partial" | "no" | "unknown";
  profile?: RoomProfile | null;
  caveats: string[];
}
export interface ParsedQuery {
  rooms?: RoomRequest[];
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
  similar_to?: string | null;
  similar_to_name?: string | null;
  /** Recognised, but the demo region has no data, so they are not ranked. */
  no_data?: Criterion[];
  no_data_must?: Criterion[];
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
  hotelier_confirmed?: boolean;
  basis?: "calculation" | "open_data" | "hotelier" | "mixed" | "unknown";
  disputed?: boolean;
}
export interface SearchHit {
  room_plan?: RoomPlanItem[];
  room_profile?: RoomProfile | null;
  hotel: Hotel;
  score: number;
  group: "all" | "almost" | "unknown" | "browse";
  must_satisfied: number;
  must_total: number;
  verdicts: Verdict[];
  room_recommendation: string | null;
  disappointments: string[];
  summary: string;
  why_not_higher?: string[];
  similarity_reasons?: string[];
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
  requested_scopes?: Scope[];
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
  room_plan?: RoomPlanItem[];
  room_profile?: RoomProfile | null;
  hotel: Hotel;
  facts: Fact[];
  claims: Claim[];
  conflicts: Conflict[];
  questions: Question[];
  verdicts: Verdict[];
  room_recommendation: string;
  disappointments: string[];
  geo: Geo;
  evidence_deferred?: boolean;
  geometry_deferred?: boolean;
}
export interface FactEvidence {
  fact: Fact;
  claims: Claim[];
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
  disposition?: "parsed" | "unknown" | "clarification";
  created_at?: string;
  created_claim_ids: string[];
}
export interface PassportItem {
  room_label?: string | null;
  room_index?: number | null;
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
  room_requests?: RoomRequest[];
  room_plan?: RoomPlanItem[];
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
  display_name?: string | null;
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
  answers?: Answer[];
  answered_questions?: Question[];
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
  nearby?: GeoJSON.FeatureCollection<
    GeoJSON.Point,
    {
      kind: string;
      name: string;
      osm_id: string;
      characteristic_id: string;
      distance_m?: number | null;
      source_ids?: string[];
    }
  >;
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

export interface FactProvenance {
  unit: "materialized_facts";
  known: number;
  non_synthetic: number;
  /** Includes mixed; mixed is a subset, never add it again. */
  synthetic: number;
  mixed: number;
  unknown_stale_disputed: number;
  synthetic_share: number | null;
}
export interface FilterViewCounts {
  positive: number;
  model_negative: number;
  unknown: number;
  partial_positive: number;
}
export interface FilterScopedCounts extends FilterViewCounts {
  observed_positive_only_at_or_above_k: number;
  model_only_at_or_above_k: number;
  observed_positive_only_one_facade: number;
  model_only_one_facade: number;
  complete_modeled_domain: number;
  complete_known_slots: number;
}
export interface FilterComparison {
  schema_version: 1;
  generated_at: string;
  seed_version: string;
  settings: {
    radius_m: number;
    month: number;
    k_floor: number;
    confidence_floor: number;
    negative_confidence_floor: number;
    search_min_floor: number;
    distance_method: string;
    search_method: string;
    input_policy: string;
    reproduce: string;
  };
  population: {
    hotels: number;
    known_floor_count: number;
    unknown_floor_count: number;
    distance_unknown: number;
  };
  baseline: {
    within_radius: number;
    outside_radius: number;
    views_within_radius: Record<"sea_view" | "sunset_view", FilterViewCounts>;
  };
  scoped: Record<"sea_view" | "sunset_view", FilterScopedCounts>;
  search: {
    compatible_supported: number;
    partial_supported: number;
    within_radius_supported: number;
    outside_radius_supported: number;
    outside_radius_partial: number;
    scope_conflict: number;
    unknown_or_unsupported: number;
  };
  examples: Json;
  limitations: string[];
  inputs_sha256: Record<string, string>;
}
export interface QualityReport extends Json {
  provenance?: FactProvenance;
  filter_comparison?: FilterComparison | null;
}

export interface RoomAdviceOption {
  scope: RoomScope;
  fact_ids: string[];
  evidence_refs: string[];
  status: "yes" | "partial";
  synthetic: boolean;
}
export interface RoomAdvice {
  id: string;
  characteristic_id: string;
  title: string;
  text: string;
  options: RoomAdviceOption[];
  fact_ids: string[];
  evidence_refs: string[];
  status: "yes" | "partial";
  synthetic: boolean;
  caveats: string[];
}
