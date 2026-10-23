"""currencies, optional prices, quote requests

Makes product_variants.price nullable (a SKU with no price is sold on request),
adds per-SKU currency and condition, a variant_prices table for per-currency
amounts, a currencies table, and the quote-request tables.

Revision ID: cbd32b1f17f8
Revises: 0002_contact_messages
Create Date: 2026-10-22 15:06:50.673210
"""
from typing import Sequence, Union

from decimal import Decimal

from alembic import op
import sqlalchemy as sa


revision: str = '0003_currency_quotes'
down_revision: Union[str, None] = '0002_contact_messages'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('currencies',
    sa.Column('code', sa.String(length=3), nullable=False),
    sa.Column('name', sa.String(length=60), nullable=False),
    sa.Column('rate_from_base', sa.Numeric(precision=20, scale=10), nullable=False),
    sa.Column('minor_units', sa.Integer(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('sort_order', sa.Integer(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.PrimaryKeyConstraint('code')
    )
    op.create_table('quote_requests',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('reference', sa.String(length=32), nullable=False),
    sa.Column('full_name', sa.String(length=160), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('phone', sa.String(length=40), nullable=True),
    sa.Column('company', sa.String(length=160), nullable=True),
    sa.Column('country', sa.String(length=80), nullable=True),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_quote_requests_email'), 'quote_requests', ['email'], unique=False)
    op.create_index(op.f('ix_quote_requests_reference'), 'quote_requests', ['reference'], unique=True)
    op.create_index(op.f('ix_quote_requests_status'), 'quote_requests', ['status'], unique=False)
    op.create_table('quote_request_items',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('request_id', sa.Integer(), nullable=False),
    sa.Column('variant_id', sa.Integer(), nullable=True),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('product_name', sa.String(length=255), nullable=False),
    sa.Column('part_number', sa.String(length=80), nullable=False),
    sa.ForeignKeyConstraint(['request_id'], ['quote_requests.id'], ),
    sa.ForeignKeyConstraint(['variant_id'], ['product_variants.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('variant_prices',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('variant_id', sa.Integer(), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('amount', sa.Numeric(precision=18, scale=2), nullable=False),
    sa.ForeignKeyConstraint(['variant_id'], ['product_variants.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('variant_id', 'currency', name='uq_variant_currency')
    )
    op.add_column(
        'product_variants',
        sa.Column('currency', sa.String(length=3), nullable=False, server_default='IRR'),
    )
    op.add_column(
        'product_variants',
        sa.Column('condition', sa.String(length=20), nullable=False, server_default='new'),
    )
    op.alter_column('product_variants', 'price',
               existing_type=sa.NUMERIC(precision=12, scale=2),
               type_=sa.Numeric(precision=18, scale=2),
               nullable=True)
    op.alter_column('product_variants', 'compare_at_price',
               existing_type=sa.NUMERIC(precision=12, scale=2),
               type_=sa.Numeric(precision=18, scale=2),
               existing_nullable=True)


    # Seed the supported currencies. Rates are indicative starting values an
    # operator is expected to correct from the admin panel — they are not fetched
    # live, so that a quoted figure stays put for the life of the quote.
    op.bulk_insert(
        sa.table(
            "currencies",
            sa.column("code", sa.String),
            sa.column("name", sa.String),
            sa.column("rate_from_base", sa.Numeric),
            sa.column("minor_units", sa.Integer),
            sa.column("is_active", sa.Boolean),
            sa.column("sort_order", sa.Integer),
        ),
        [
            {"code": "IRR", "name": "Iranian Rial", "rate_from_base": 1,
             "minor_units": 0, "is_active": True, "sort_order": 1},
            {"code": "USD", "name": "US Dollar", "rate_from_base": Decimal("0.0000016"),
             "minor_units": 2, "is_active": True, "sort_order": 2},
            {"code": "AED", "name": "UAE Dirham", "rate_from_base": Decimal("0.0000059"),
             "minor_units": 2, "is_active": True, "sort_order": 3},
        ],
    )


def downgrade() -> None:
    op.alter_column('product_variants', 'compare_at_price',
               existing_type=sa.Numeric(precision=18, scale=2),
               type_=sa.NUMERIC(precision=12, scale=2),
               existing_nullable=True)
    op.alter_column('product_variants', 'price',
               existing_type=sa.Numeric(precision=18, scale=2),
               type_=sa.NUMERIC(precision=12, scale=2),
               nullable=False)
    op.drop_column('product_variants', 'condition')
    op.drop_column('product_variants', 'currency')
    op.drop_table('variant_prices')
    op.drop_table('quote_request_items')
    op.drop_index(op.f('ix_quote_requests_status'), table_name='quote_requests')
    op.drop_index(op.f('ix_quote_requests_reference'), table_name='quote_requests')
    op.drop_index(op.f('ix_quote_requests_email'), table_name='quote_requests')
    op.drop_table('quote_requests')
    op.drop_table('currencies')
