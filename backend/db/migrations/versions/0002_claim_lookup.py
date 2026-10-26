"""Index immutable evidence lookups used by cards and incremental refresh."""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE INDEX claims_hotel_lookup ON claims ((payload->>'hotel_id'))")
    op.execute("CREATE INDEX change_events_hotel_lookup ON change_events USING gin ((payload->'affected_hotel_ids'))")


def downgrade():
    op.execute("DROP INDEX change_events_hotel_lookup")
    op.execute("DROP INDEX claims_hotel_lookup")
