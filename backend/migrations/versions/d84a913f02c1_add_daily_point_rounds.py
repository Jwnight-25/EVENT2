"""Append auditable daily rank point rounds without changing prior outcomes."""

from alembic import op
import sqlalchemy as sa

revision = "d84a913f02c1"
down_revision = "b63e32159f01"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "daily_point_rounds",
        sa.Column("batch_id", sa.String(36), sa.ForeignKey("daily_batches.id"), nullable=False),
        sa.Column("actual_revision_id", sa.String(36), sa.ForeignKey("bar_revisions.id"), nullable=False),
        sa.Column("policy", sa.String(60), nullable=False),
        sa.Column("cohort_checksum", sa.String(64), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.UniqueConstraint("batch_id", "actual_revision_id", "policy", "cohort_checksum"),
    )
    op.create_index("ix_daily_point_rounds_batch_id", "daily_point_rounds", ["batch_id"])


def downgrade():
    op.drop_index("ix_daily_point_rounds_batch_id", table_name="daily_point_rounds")
    op.drop_table("daily_point_rounds")
