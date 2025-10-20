"""Add security_universe table

Revision ID: 002
Revises: 001
Create Date: 2025-09-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '002'
down_revision: Union[str, None] = '001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create security_universe table
    op.create_table('security_universe',
        sa.Column('symbol', sa.String(32), primary_key=True),
        sa.Column('market', sa.String(8), nullable=False),
        sa.Column('name_cn', sa.String(128)),
        sa.Column('name_en', sa.String(128)),
        sa.Column('name_hk', sa.String(128)),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text('CURRENT_TIMESTAMP'))
    )


def downgrade() -> None:
    op.drop_table('security_universe')