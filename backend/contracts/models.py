"""Version 1 shared wire models. Only the orchestrator edits this module."""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.contracts.schema_safety import check_schema_safety

Json = dict[str, Any]
Side = Literal["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
Mode = Literal["offline", "cache", "live"]


def uid() -> str:
    return str(uuid4())


def now() -> datetime:
    return datetime.now(UTC)


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Scope(Model):
    type: Literal["hotel", "facade", "floor_range", "room_type"] = "hotel"
    facade: Side | None = None
    floor_min: int | None = Field(default=None, ge=1, le=100)
    floor_max: int | None = Field(default=None, ge=1, le=100)
    room_type: str | None = None
    month: int | None = Field(default=None, ge=1, le=12)

    @model_validator(mode="after")
    def bounds(self) -> Scope:
        if self.floor_min and self.floor_max and self.floor_min > self.floor_max:
            raise ValueError("floor_min must not exceed floor_max")
        if self.type in {"facade", "floor_range"} and self.facade is None:
            raise ValueError("facade is required for scoped room evidence")
        if self.type == "room_type" and not self.room_type:
            raise ValueError("room_type is required")
        return self


class Evidence(Model):
    id: str = Field(default_factory=uid)
    kind: Literal["calculation", "open_data", "quote", "hotelier", "simulation", "mock"]
    label: str = Field(min_length=1)
    source_ids: list[str] = Field(min_length=1)
    url: str | None = None
    artifact: str | None = None
    quote: str | None = None
    message_id: str | None = None
    inputs: Json = Field(default_factory=dict)
    output: Json = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def reference(self) -> Evidence:
        if not any([self.url, self.artifact, self.quote, self.message_id]):
            raise ValueError("evidence needs a URL, artifact, quote or message reference")
        if self.kind == "quote" and not self.quote:
            raise ValueError("text extraction requires an exact quote")
        return self


class Claim(Model):
    id: str = Field(default_factory=uid)
    hotel_id: str
    characteristic_id: str
    spec_version: int = Field(default=1, ge=1)
    scope: Scope = Field(default_factory=Scope)
    value: Any
    unit: str | None = None
    source: str = Field(min_length=1)
    method: str = Field(min_length=1)
    evidence: list[Evidence] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    observed_at: datetime
    valid_until: datetime
    run_id: str | None = None
    supersedes: str | None = None
    supersedes_ids: list[str] = Field(default_factory=list)
    synthetic: bool = False

    @model_validator(mode="after")
    def dates(self) -> Claim:
        if self.observed_at.tzinfo is None or self.valid_until.tzinfo is None:
            raise ValueError("timestamps must include a timezone")
        if self.valid_until <= self.observed_at:
            raise ValueError("valid_until must follow observed_at")
        return self


class Fact(Model):
    id: str = Field(default_factory=uid)
    hotel_id: str
    characteristic_id: str
    scope: Scope = Field(default_factory=Scope)
    value: Any = None
    confidence: float = Field(default=0, ge=0, le=1)
    status: Literal["computed", "confirmed", "disputed", "unknown", "stale"] = "unknown"
    resolution: Literal["single", "weighted_vote", "hotelier_override", "scope_split", "unknown"] = "unknown"
    supporting_claim_ids: list[str] = Field(default_factory=list)
    conflicting_claim_ids: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=now)
    valid_until: datetime | None = None
    synthetic: bool = False


class Conflict(Model):
    id: str = Field(default_factory=uid)
    hotel_id: str
    characteristic_id: str
    claim_ids: list[str] = Field(min_length=2)
    kind: Literal["value", "scope", "temporal", "height_quality"]
    status: Literal["open", "auto_resolved", "asked", "resolved"] = "open"
    resolution_note: str
    question_id: str | None = None
    synthetic: bool = False


class ToolSpec(Model):
    id: str
    version: str = "1"
    description: str
    parameters_schema: Json = Field(default_factory=lambda: {"type": "object"})
    output_schema: Json = Field(default_factory=lambda: {"type": "object"})
    cost: float = Field(default=1, gt=0)
    prior_reliability: float = Field(default=.7, ge=0, le=1)
    offline: bool = True


class ToolCall(Model):
    tool: str
    params: Json = Field(default_factory=dict)
    rationale: str = "Детерминированный план по спецификации"
    when: str | None = None


class ToolContext(Model):
    hotel_id: str
    characteristic_id: str
    run_id: str
    month: int = Field(default=9, ge=1, le=12)
    scope: Scope = Field(default_factory=Scope)


class ToolResult(Model):
    claims: list[Claim] = Field(default_factory=list)
    artifacts: Json = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class Tool(Protocol):
    spec: ToolSpec

    def run(self, context: ToolContext, params: Json) -> ToolResult: ...


class SearchSpec(Model):
    kind: Literal["boolean", "numeric", "categorical", "composite"] = "boolean"
    field: str = "available"
    default_op: Literal["eq", "lte", "gte", "contains"] = "eq"
    default_value: Any = True
    unit: str | None = None
    explain_ok: str = "Соответствует условию"
    explain_bad: str = "Условие не выполняется"


class CharacteristicSpec(Model):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    version: int = Field(default=1, ge=1)
    name_ru: str
    description: str
    category: str = "Окружение"
    synonyms: list[str] = Field(default_factory=list)
    scope: Literal["hotel", "facade", "floor_range", "room_type"] = "hotel"
    value_schema: Json
    methods: list[ToolCall] = Field(min_length=1)
    combine: str | None = None
    ttl_days: int = Field(default=180, ge=1, le=3650)
    change_triggers: list[Json] = Field(default_factory=list)
    search: SearchSpec = Field(default_factory=SearchSpec)
    hotelier_answerable: bool = True
    status: Literal["draft", "active"] = "active"

    @model_validator(mode="after")
    def safe_schema(self) -> CharacteristicSpec:
        check_schema_safety(self.value_schema)
        return self


class Question(Model):
    id: str = Field(default_factory=uid)
    hotel_id: str
    characteristic_ids: list[str] = Field(min_length=1)
    text: str
    hypothesis: str | None = None
    options: list[str] = Field(default_factory=lambda: ["Да", "Нет", "Частично", "Не знаю"])
    priority: float = 0
    reason: Literal["unknown", "disputed", "low_confidence", "guest", "conflict"] = "unknown"
    status: Literal["pending", "answered", "expired"] = "pending"
    evidence_refs: list[str] = Field(default_factory=list)
    demand_estimate: int = 0
    demand_synthetic: bool = True
    asked_at: datetime = Field(default_factory=now)


class FieldUpdate(Model):
    characteristic_id: str
    scope: Scope = Field(default_factory=Scope)
    value: Any
    quote: str = Field(min_length=1)
    confidence: float = Field(default=.85, ge=0, le=1)


class Answer(Model):
    id: str = Field(default_factory=uid)
    question_id: str
    raw_text: str
    updates: list[FieldUpdate] = Field(default_factory=list)
    unknown_fields: list[str] = Field(default_factory=list)
    needs_clarification: str | None = None
    parser: Mode = "offline"
    confirmed: bool = False
    synthetic: bool = False
    created_claim_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=now)

    @model_validator(mode="after")
    def quotes(self) -> Answer:
        if any(update.quote not in self.raw_text for update in self.updates):
            raise ValueError("all extracted quotes must be substrings of raw_text")
        return self


class Criterion(Model):
    characteristic_id: str
    label: str
    op: Literal["eq", "lte", "gte", "contains"] = "eq"
    value: Any = True
    field: str | None = None
    weight: float = Field(default=1, gt=0, le=10)
    scope: Scope | None = None
    no_stairs: bool = False
    no_crossings: bool = False


class ParsedQuery(Model):
    text: str
    must: list[Criterion] = Field(default_factory=list)
    nice: list[Criterion] = Field(default_factory=list)
    check_in: date | None = None
    check_out: date | None = None
    month: int = Field(default=9, ge=1, le=12)
    persona: str | None = None
    pet_weight_kg: float | None = Field(default=None, ge=0, le=100)
    min_floor: int | None = Field(default=None, ge=1, le=100)
    facade: Side | None = None
    unmapped: list[str] = Field(default_factory=list)
    clarifications: list[str] = Field(default_factory=list, max_length=2)
    mode: Mode = "offline"
    similar_to: str | None = None
    similar_to_name: str | None = None
    # Recognized conditions without any data in the demo region; not used for ranking.
    no_data: list[Criterion] = Field(default_factory=list)


class SearchRequest(Model):
    text: str = Field(default="", max_length=2000)
    check_in: date | None = None
    check_out: date | None = None
    parsed: ParsedQuery | None = None
    similar_to: str | None = None
    limit: int = Field(default=20, ge=1, le=250)

    @model_validator(mode="after")
    def dates(self) -> SearchRequest:
        if self.check_in and self.check_out and self.check_out <= self.check_in:
            raise ValueError("check_out must follow check_in")
        return self


class CriterionVerdict(Model):
    characteristic_id: str
    label: str
    required: bool = False
    status: Literal["yes", "partial", "no", "unknown"]
    symbol: Literal["✓", "≈", "✗", "?"]
    text: str
    confidence: float = Field(ge=0, le=1)
    fact_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    caveat: str | None = None
    synthetic: bool = False
    disputed: bool = False


class ComparisonRequest(Model):
    ids: list[str] = Field(min_length=2, max_length=4)
    parsed: ParsedQuery | None = None

    @model_validator(mode="after")
    def unique(self) -> ComparisonRequest:
        if len(set(self.ids)) != len(self.ids):
            raise ValueError("Choose distinct hotels")
        return self


class Hotel(Model):
    id: str
    name: str
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    address: str = "Сочи"
    stars: int | None = Field(default=None, ge=0, le=5)
    price_rub: int | None = None
    basic_fields_source: str = "mock_basic_card"
    source_ids: list[str] = Field(default_factory=list)
    osm_id: str | None = None
    overture_id: str | None = None
    website: str | None = None
    floors: int | None = None
    footprint: Json | None = None
    image: str | None = None
    image_attribution: str | None = None
    room_types: list[Json] = Field(default_factory=list)
    tags: Json = Field(default_factory=dict)


class SearchHit(Model):
    hotel: Hotel
    score: float
    group: Literal["all", "almost", "unknown", "browse"]
    must_satisfied: int = 0
    must_total: int = 0
    verdicts: list[CriterionVerdict] = Field(default_factory=list)
    room_recommendation: str | None = None
    disappointments: list[str] = Field(default_factory=list)
    summary: str = ""


class SearchResult(Model):
    parsed: ParsedQuery
    results: list[SearchHit]
    total: int
    latency_ms: float
    run_id: str | None = None
    mode: Mode = "offline"


class HotelDetail(Model):
    hotel: Hotel
    facts: list[Fact] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    questions: list[Question] = Field(default_factory=list)
    verdicts: list[CriterionVerdict] = Field(default_factory=list)
    room_recommendation: str = "Уточните сторону и этаж номера у отеля"
    disappointments: list[str] = Field(default_factory=list)
    geo: Json = Field(default_factory=dict)


class PassportItem(Model):
    id: str = Field(default_factory=uid)
    characteristic_id: str
    text: str
    critical: bool = True
    scope: Scope = Field(default_factory=Scope)
    fact_ids: list[str] = Field(default_factory=list)
    status: Literal["pending", "confirmed", "alternative", "at_risk", "declined"] = "pending"
    response: str | None = None


class ExpectationPassport(Model):
    id: str = Field(default_factory=uid)
    hotel_id: str
    guest_name: str = "Маша"
    check_in: date | None = None
    check_out: date | None = None
    items: list[PassportItem] = Field(min_length=1)
    status: Literal["draft", "sent", "confirmed", "alternative", "at_risk"] = "sent"
    room_number: str | None = None
    timeline: list[Json] = Field(default_factory=list)
    notifications: list[Json] = Field(default_factory=list)
    synthetic_booking: bool = True
    created_at: datetime = Field(default_factory=now)


class ChangeEvent(Model):
    id: str = Field(default_factory=uid)
    kind: str
    title: str
    geometry: Json | None = None
    payload: Json = Field(default_factory=dict)
    affected_hotel_ids: list[str] = Field(default_factory=list)
    before: list[Fact] = Field(default_factory=list)
    after: list[Fact] = Field(default_factory=list)
    notifications: list[Json] = Field(default_factory=list)
    synthetic: bool = True
    detected_at: datetime = Field(default_factory=now)


class AgentStep(Model):
    id: str = Field(default_factory=uid)
    run_id: str
    seq: int = Field(ge=0)
    agent: str
    type: str
    name: str
    input: Json = Field(default_factory=dict)
    output: Json = Field(default_factory=dict)
    rationale: str
    evidence_refs: list[str] = Field(default_factory=list)
    duration_ms: float = 0
    mode: Mode = "offline"
    cache_hit: bool = False
    error: str | None = None
    created_at: datetime = Field(default_factory=now)


class AgentRun(Model):
    id: str = Field(default_factory=uid)
    kind: Literal["collect", "interview", "search", "compile_spec", "refresh", "backfill"]
    hotel_id: str | None = None
    characteristic_id: str | None = None
    trigger: str = "user"
    status: Literal["running", "completed", "failed", "waiting"] = "running"
    llm_mode: Mode = "offline"
    started_at: datetime = Field(default_factory=now)
    finished_at: datetime | None = None
    steps: list[AgentStep] = Field(default_factory=list)
    error: str | None = None


class Source(Model):
    id: str
    name: str
    kind: str
    url: str
    license: str
    attribution: str
    observed_at: datetime
    retrieved_at: datetime
    used: bool = True
    notes: str = ""
    alpha: float = 7
    beta: float = 3


class AnswerRequest(Model):
    text: str = Field(min_length=1, max_length=10000)
    confirm: bool = False
    synthetic: bool = False


class PassportCreate(Model):
    hotel_id: str
    guest_name: str = "Маша"
    check_in: date | None = None
    check_out: date | None = None
    items: list[PassportItem] = Field(default_factory=list)
    parsed: ParsedQuery | None = None


class PassportAction(Model):
    action: Literal["confirm", "alternative", "feedback"]
    room_number: str | None = None
    text: str = ""
    matched: bool | None = None


class CompileRequest(Model):
    text: str = Field(default="", max_length=5000)
    yaml_text: str | None = None


class RunRequest(Model):
    hotel_id: str
    characteristic_ids: list[str] = Field(default_factory=list)


class ChangeRequest(Model):
    hotel_id: str | None = None


class AskRequest(Model):
    hotel_id: str
    characteristic_id: str


class LoginRequest(Model):
    username: str
    password: str


class LoginResponse(Model):
    token: str
    username: str
    synthetic: Literal[True] = True
    role: Literal["demo_admin", "hotelier"]
    hotel_ids: list[str] = Field(default_factory=list)


class LogoutResponse(Model):
    logged_out: Literal[True] = True
    synthetic: Literal[True] = True


class LabelRequest(Model):
    hotel_id: str
    characteristic_id: str
    scope: Scope = Field(default_factory=Scope)
    value: Any
    evidence_url: str
    note: str = ""
    labeler: str = "manual"

