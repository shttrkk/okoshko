"""Validated JSON envelopes, PostGIS lookup, immutable provenance ledger."""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TABLES = (
    "regions", "sources", "hotels", "hotel_facades", "room_types", "buildings",
    "osm_features", "characteristics", "characteristic_versions", "agent_runs",
    "agent_steps", "claims", "facts", "conflicts", "questions", "answers",
    "change_events", "jobs", "tool_cache", "llm_cache", "search_queries",
    "eval_labels", "eval_results", "expectation_passports", "passport_items",
)


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    for table in TABLES:
        op.execute(f"""CREATE TABLE {table} (
            id text PRIMARY KEY,
            payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CHECK (payload->>'id' = id)
        )""")
    op.execute("""ALTER TABLE hotels ADD COLUMN geom geometry(Point,4326)
        GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(
            (payload->>'lon')::double precision,(payload->>'lat')::double precision),4326)) STORED""")
    op.execute("CREATE INDEX hotels_geom ON hotels USING gist(geom)")
    op.execute("""ALTER TABLE claims
        ADD COLUMN hotel_id text GENERATED ALWAYS AS (payload->>'hotel_id') STORED NOT NULL REFERENCES hotels(id),
        ADD COLUMN characteristic_id text GENERATED ALWAYS AS (payload->>'characteristic_id') STORED NOT NULL,
        ADD COLUMN source text GENERATED ALWAYS AS (payload->>'source') STORED NOT NULL,
        ADD COLUMN method text GENERATED ALWAYS AS (payload->>'method') STORED NOT NULL,
        ADD COLUMN evidence jsonb GENERATED ALWAYS AS (payload->'evidence') STORED NOT NULL,
        ADD COLUMN confidence double precision GENERATED ALWAYS AS ((payload->>'confidence')::double precision) STORED NOT NULL,
        ADD COLUMN observed_at text GENERATED ALWAYS AS (payload->>'observed_at') STORED NOT NULL,
        ADD COLUMN valid_until text GENERATED ALWAYS AS (payload->>'valid_until') STORED NOT NULL,
        ADD CHECK (length(source)>0 AND length(method)>0),
        ADD CHECK (jsonb_typeof(evidence)='array' AND jsonb_array_length(evidence)>0),
        ADD CHECK (confidence BETWEEN 0 AND 1),
        ADD CHECK (valid_until::timestamptz > observed_at::timestamptz)""")
    for table in ("facts", "conflicts", "questions", "expectation_passports"):
        op.execute(f"CREATE INDEX {table}_hotel ON {table} ((payload->>'hotel_id'))")
    for table in ("claims", "facts"):
        op.execute(f"CREATE INDEX {table}_characteristic ON {table} ((payload->>'characteristic_id'))")
    op.execute("CREATE INDEX agent_steps_run ON agent_steps ((payload->>'run_id'))")
    op.execute("CREATE UNIQUE INDEX agent_steps_seq ON agent_steps ((payload->>'run_id'), ((payload->>'seq')::int))")
    op.execute("""CREATE FUNCTION immutable_claims() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN RAISE EXCEPTION 'Claims are append-only; insert a superseding claim'; END $$""")
    op.execute("CREATE TRIGGER claims_immutable BEFORE UPDATE OR DELETE ON claims FOR EACH ROW EXECUTE FUNCTION immutable_claims()")


def downgrade():
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("DROP FUNCTION IF EXISTS immutable_claims()")

