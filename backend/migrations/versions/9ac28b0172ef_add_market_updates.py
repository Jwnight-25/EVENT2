"""Pin market sources and record daily close updates."""

from alembic import op
import sqlalchemy as sa

revision = "9ac28b0172ef"
down_revision = "ff1215287a55"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "market_feeds",
        sa.Column("stock_id", sa.String(36), sa.ForeignKey("stocks.id"), primary_key=True),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("price_basis", sa.String(16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("source_import_id", sa.String(36), sa.ForeignKey("imports.id"), nullable=False),
        sa.Column("last_completed_session", sa.String(10), nullable=True),
        sa.Column("created_at", sa.String(40), nullable=False),
    )
    op.create_table(
        "market_update_runs",
        sa.Column("stock_id", sa.String(36), sa.ForeignKey("market_feeds.stock_id"), nullable=False),
        sa.Column("session_date", sa.String(10), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("started_at", sa.String(40), nullable=False),
        sa.Column("finished_at", sa.String(40), nullable=True),
        sa.Column("error", sa.String(1000), nullable=True),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.String(40), nullable=False),
    )
    op.create_index("ix_market_update_runs_stock_id", "market_update_runs", ["stock_id"])


def downgrade():
    op.drop_index("ix_market_update_runs_stock_id", table_name="market_update_runs")
    op.drop_table("market_update_runs")
    op.drop_table("market_feeds")
