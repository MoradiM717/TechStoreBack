"""contact messages

Adds the storefront contact-form inbox (contact_messages).

Revision ID: 73e9db0cb9da
Revises: 0001_initial
Create Date: 2026-08-28 00:02:23.521342
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0002_contact_messages'
down_revision: Union[str, None] = '0001_initial'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('contact_messages',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('company', sa.String(length=160), nullable=True),
    sa.Column('topic', sa.String(length=30), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_contact_messages_email'), 'contact_messages', ['email'], unique=False)
    op.create_index(op.f('ix_contact_messages_status'), 'contact_messages', ['status'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_contact_messages_status'), table_name='contact_messages')
    op.drop_index(op.f('ix_contact_messages_email'), table_name='contact_messages')
    op.drop_table('contact_messages')
