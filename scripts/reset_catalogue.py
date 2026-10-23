#!/usr/bin/env python
"""
Delete catalogue and transaction data.

Built for one job: clearing the fictional demo catalogue out before the real
warehouse import, so the store doesn't go live with invented products beside
real stock. It refuses to run without --yes, and it tells you what it is about
to destroy first.

    python -m scripts.reset_catalogue                 # report only
    python -m scripts.reset_catalogue --yes           # catalogue only
    python -m scripts.reset_catalogue --yes --all     # catalogue + orders + enquiries

Admin users and currencies are never touched.
"""
import argparse
import sys

from app.core.database import SessionLocal
from app.models.category import Category
from app.models.contact import ContactMessage
from app.models.order import Order, OrderItem, Customer
from app.models.product import Product, ProductImage, ProductVariant, VariantPrice
from app.models.quote import QuoteRequest, QuoteRequestItem
from app.services.search_service import INDEX_NAME, es_client


def main() -> int:
    parser = argparse.ArgumentParser(description="Clear catalogue data.")
    parser.add_argument("--yes", action="store_true", help="actually delete")
    parser.add_argument("--all", action="store_true",
                        help="also delete orders, quote requests and enquiries")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        counts = {
            "products": db.query(Product).count(),
            "variants": db.query(ProductVariant).count(),
            "categories": db.query(Category).count(),
            "orders": db.query(Order).count(),
            "quote requests": db.query(QuoteRequest).count(),
            "enquiries": db.query(ContactMessage).count(),
        }
        print("Currently in the database:")
        for label, n in counts.items():
            print(f"  {n:>6}  {label}")

        if not args.yes:
            print("\nNothing deleted. Re-run with --yes to proceed"
                  " (add --all to include orders and enquiries).")
            return 0

        # Order items reference variants, so transactional rows go first or the
        # foreign keys block the catalogue delete.
        if args.all:
            db.query(QuoteRequestItem).delete(synchronize_session=False)
            db.query(QuoteRequest).delete(synchronize_session=False)
            db.query(OrderItem).delete(synchronize_session=False)
            db.query(Order).delete(synchronize_session=False)
            db.query(Customer).delete(synchronize_session=False)
            db.query(ContactMessage).delete(synchronize_session=False)
            print("\n  cleared orders, quote requests and enquiries")
        elif db.query(OrderItem).count():
            print("\nThere are order lines pointing at the catalogue. Re-run with --all"
                  " to clear those too, or keep the catalogue.", file=sys.stderr)
            return 1

        db.query(VariantPrice).delete(synchronize_session=False)
        db.query(ProductImage).delete(synchronize_session=False)
        db.query(ProductVariant).delete(synchronize_session=False)
        db.query(Product).delete(synchronize_session=False)
        # Categories are re-created by the importer from the workbook's own list.
        db.query(Category).update({Category.parent_id: None}, synchronize_session=False)
        db.query(Category).delete(synchronize_session=False)
        db.commit()
        print("  cleared the catalogue")

        try:
            es_client.indices.delete(index=INDEX_NAME, ignore=[404])
            print("  dropped the search index (the importer rebuilds it)")
        except Exception as exc:  # noqa: BLE001
            print(f"  search index not dropped ({exc}); reindex after importing")
        return 0
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        print(f"Failed, nothing deleted: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
