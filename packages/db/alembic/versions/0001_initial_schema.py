"""Initial TradzLog schema.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-05-09
"""

from collections.abc import Sequence

from alembic import op

from tradzlog_db.base import Base
from tradzlog_db.models import *  # noqa: F403

revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
