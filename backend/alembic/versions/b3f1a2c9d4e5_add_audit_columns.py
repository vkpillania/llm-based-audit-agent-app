"""add audit_verdict and audit_risk_score columns

Revision ID: b3f1a2c9d4e5
Revises: ce0603277109
Create Date: 2026-06-26 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b3f1a2c9d4e5'
down_revision: Union[str, Sequence[str], None] = 'ce0603277109'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('invoices', sa.Column('audit_verdict', sa.String(length=16), nullable=True))
    op.add_column('invoices', sa.Column('audit_risk_score', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('invoices', 'audit_risk_score')
    op.drop_column('invoices', 'audit_verdict')
