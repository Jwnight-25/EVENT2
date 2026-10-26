"""Keep stock data while removing/restoring watchlist entries."""

from alembic import op
import sqlalchemy as sa

revision = "e12a562e8c39"
down_revision = "d84a913f02c1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("stocks", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.create_table(
        "stock_watch_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("stock_id", sa.String(36), sa.ForeignKey("stocks.id"), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("changed_at", sa.String(40), nullable=False),
    )
    op.create_index("ix_stock_watch_events_stock_id", "stock_watch_events", ["stock_id"])


def downgrade():
    op.drop_index("ix_stock_watch_events_stock_id", table_name="stock_watch_events")
    op.drop_table("stock_watch_events")
    op.drop_column("stocks", "is_active")
