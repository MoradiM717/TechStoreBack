#!/usr/bin/env python
"""
Seed the categories and a small, realistic product catalogue, then push everything
to Elasticsearch.

Idempotent: matches on slug, so re-running updates rather than duplicating.

    python -m scripts.seed_demo
    docker compose exec backend python -m scripts.seed_demo

Product imagery is generated locally as flat SVG placeholders under `media/products/`
so a fresh clone has something to render without any asset pipeline. Replace them
with real photography via the admin panel's image upload.
"""
import sys
from decimal import Decimal
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.category import Category
from app.models.product import Product, ProductImage, ProductVariant
from app.services.search_service import reindex_all

CATEGORIES = [
    {"slug": "servers", "name": "Servers", "parent": None},
    {"slug": "gpu-servers", "name": "GPU Servers", "parent": "servers"},
    {"slug": "storage-servers", "name": "Storage Servers", "parent": "servers"},
    {"slug": "cpu", "name": "CPUs", "parent": None},
    {"slug": "ram", "name": "RAM", "parent": None},
]

# --- Flat SVG placeholders, one motif per category -------------------------
# Background is transparent and the strokes sit at a mid tone that reads on both
# the light and the dark storefront palette, so one file serves both themes.
PALETTE = {"line": "#767E8C", "ink": "#767E8C", "accent": "#3D7BFF"}

SVG_MOTIFS = {
    "gpu-servers": """
      <rect x="90" y="150" width="620" height="500" rx="10" fill="none" stroke="{line}" stroke-width="2" opacity="0.5"/>
      {rows}
      <rect x="120" y="185" width="90" height="26" fill="{accent}" opacity="0.5"/>
    """,
    "storage-servers": """
      <rect x="110" y="170" width="580" height="460" rx="10" fill="none" stroke="{line}" stroke-width="2" opacity="0.5"/>
      {bays}
    """,
    "cpu": """
      <rect x="230" y="230" width="340" height="340" rx="14" fill="none" stroke="{line}" stroke-width="2" opacity="0.5"/>
      <rect x="300" y="300" width="200" height="200" rx="6" fill="none" stroke="{accent}" stroke-width="2" opacity="0.7"/>
      {pins}
    """,
    "ram": """
      <rect x="120" y="330" width="560" height="150" rx="6" fill="none" stroke="{line}" stroke-width="2" opacity="0.5"/>
      {chips}
      <rect x="150" y="470" width="500" height="10" fill="{accent}" opacity="0.35"/>
    """,
}


def _motif(slug: str) -> str:
    if slug == "gpu-servers":
        rows = "".join(
            f'<rect x="120" y="{y}" width="560" height="26" fill="none" stroke="{PALETTE["line"]}" stroke-width="1.5" opacity="0.45"/>'
            for y in range(185, 620, 55)
        )
        return SVG_MOTIFS[slug].format(rows=rows, **PALETTE)
    if slug == "storage-servers":
        bays = "".join(
            f'<rect x="{x}" y="{y}" width="126" height="86" fill="none" stroke="{PALETTE["line"]}" stroke-width="1.5" opacity="0.45"/>'
            for y in range(200, 600, 100)
            for x in range(140, 660, 140)
        )
        return SVG_MOTIFS[slug].format(bays=bays, **PALETTE)
    if slug == "cpu":
        pins = "".join(
            f'<rect x="{x}" y="{y}" width="8" height="18" fill="{PALETTE["line"]}" opacity="0.45"/>'
            for x in range(250, 560, 26)
            for y in (200, 574)
        )
        return SVG_MOTIFS[slug].format(pins=pins, **PALETTE)
    chips = "".join(
        f'<rect x="{x}" y="360" width="58" height="90" fill="none" stroke="{PALETTE["line"]}" stroke-width="1.5" opacity="0.45"/>'
        for x in range(150, 660, 72)
    )
    return SVG_MOTIFS["ram"].format(chips=chips, **PALETTE)


def write_placeholder(category_slug: str, product_slug: str, label: str) -> str:
    """Writes media/products/seed-<product-slug>.svg and returns its public URL."""
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 800" width="800" height="800" role="img" aria-label="{label}">
  {_motif(category_slug)}
  <text x="400" y="726" text-anchor="middle" font-family="JetBrains Mono, ui-monospace, monospace"
        font-size="20" letter-spacing="4" fill="{PALETTE['ink']}">{label.upper()}</text>
</svg>
"""
    destination = Path(settings.media_root) / "products" / f"seed-{product_slug}.svg"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(svg, encoding="utf-8")
    return f"{settings.public_base_url.rstrip('/')}/media/products/seed-{product_slug}.svg"


# --- Catalogue -------------------------------------------------------------
PRODUCTS = [
    {
        "slug": "dell-poweredge-r760xa",
        "name": "Dell PowerEdge R760xa",
        "brand": "Dell",
        "category": "gpu-servers",
        "description": (
            "2U dual-socket accelerator platform for training and inference workloads. "
            "Four dual-width GPUs, 32 DDR5 DIMM slots, redundant 2400W Titanium PSUs."
        ),
        "specs": {
            "cpu_model": "Intel Xeon Gold 6448Y",
            "cpu_cores": 32,
            "cpu_sockets": 2,
            "gpu_model": "NVIDIA H100 80GB PCIe",
            "gpu_count": 4,
            "psu_watts": 2400,
            "form_factor": "2U Rack",
            "memory_slots": 32,
            "max_memory_tb": 4,
        },
        "variants": [
            ("R760XA-256-2TB", {"ram_gb": 256, "storage": "2TB NVMe"}, "38499.00", 6),
            ("R760XA-512-4TB", {"ram_gb": 512, "storage": "4TB NVMe"}, "44950.00", 3),
            ("R760XA-1024-8TB", {"ram_gb": 1024, "storage": "8TB NVMe"}, "58200.00", 0),
        ],
    },
    {
        "slug": "supermicro-as-4125gs-tnrt",
        "name": "Supermicro AS-4125GS-TNRT",
        "brand": "Supermicro",
        "category": "gpu-servers",
        "description": (
            "4U dual-root GPU system taking eight double-width accelerators with direct "
            "PCIe 5.0 x16 links to each card."
        ),
        "specs": {
            "cpu_model": "AMD EPYC 9354",
            "cpu_cores": 32,
            "cpu_sockets": 2,
            "gpu_model": "NVIDIA H100 NVL",
            "gpu_count": 8,
            "psu_watts": 5250,
            "form_factor": "4U Rack",
            "memory_slots": 24,
            "max_memory_tb": 6,
        },
        "variants": [
            ("AS4125-512-4TB", {"ram_gb": 512, "storage": "4TB NVMe"}, "89900.00", 2),
            ("AS4125-1024-8TB", {"ram_gb": 1024, "storage": "8TB NVMe"}, "104500.00", 1),
        ],
    },
    {
        "slug": "hpe-proliant-dl380-gen11",
        "name": "HPE ProLiant DL380 Gen11",
        "brand": "HPE",
        "category": "gpu-servers",
        "description": "2U workhorse with optional single-width accelerators; the safe default for mixed virtualisation and light inference.",
        "specs": {
            "cpu_model": "Intel Xeon Silver 4410Y",
            "cpu_cores": 12,
            "cpu_sockets": 2,
            "gpu_model": "NVIDIA L4 24GB",
            "gpu_count": 2,
            "psu_watts": 1600,
            "form_factor": "2U Rack",
            "memory_slots": 32,
            "max_memory_tb": 8,
        },
        "variants": [
            ("DL380G11-128-2TB", {"ram_gb": 128, "storage": "2TB SATA SSD"}, "12480.00", 14),
            ("DL380G11-256-4TB", {"ram_gb": 256, "storage": "4TB NVMe"}, "16990.00", 9),
        ],
    },
    {
        "slug": "supermicro-ssg-641e-e1cr36l",
        "name": "Supermicro SSG-641E-E1CR36L",
        "brand": "Supermicro",
        "category": "storage-servers",
        "description": "4U top-loading 36-bay chassis for bulk capacity, with dual-port SAS expanders and NVMe cache tier.",
        "specs": {
            "cpu_model": "Intel Xeon Gold 5416S",
            "cpu_cores": 16,
            "drive_bays": 36,
            "controller": "Broadcom 3808 tri-mode",
            "psu_watts": 1600,
            "form_factor": "4U Rack",
            "networking": "2x 10GbE + OCP 3.0",
        },
        "variants": [
            ("SSG641-256-360TB", {"ram_gb": 256, "storage": "36x 10TB SAS"}, "31750.00", 4),
            ("SSG641-512-720TB", {"ram_gb": 512, "storage": "36x 20TB SAS"}, "48900.00", 2),
        ],
    },
    {
        "slug": "amd-epyc-9654",
        "name": "AMD EPYC 9654",
        "brand": "AMD",
        "category": "cpu",
        "description": "96-core Genoa flagship. 12-channel DDR5 and 128 PCIe 5.0 lanes per socket.",
        "specs": {
            "socket": "SP5",
            "cores": 96,
            "threads": 192,
            "base_clock_ghz": 2.4,
            "boost_clock_ghz": 3.7,
            "tdp_watts": 360,
            "l3_cache_mb": 384,
            "memory_channels": 12,
        },
        "variants": [
            ("EPYC-9654-TRAY", {"packaging": "Tray"}, "10890.00", 18),
            ("EPYC-9654-BOX", {"packaging": "Boxed + heatsink"}, "11340.00", 7),
        ],
    },
    {
        "slug": "intel-xeon-gold-6548y",
        "name": "Intel Xeon Gold 6548Y+",
        "brand": "Intel",
        "category": "cpu",
        "description": "32-core Emerald Rapids part with AMX acceleration — a balanced pick for virtualisation hosts.",
        "specs": {
            "socket": "LGA4677",
            "cores": 32,
            "threads": 64,
            "base_clock_ghz": 2.5,
            "boost_clock_ghz": 4.1,
            "tdp_watts": 250,
            "l3_cache_mb": 60,
            "memory_channels": 8,
        },
        "variants": [
            ("XEON-6548Y-TRAY", {"packaging": "Tray"}, "3720.00", 25),
        ],
    },
    {
        "slug": "amd-epyc-9354p",
        "name": "AMD EPYC 9354P",
        "brand": "AMD",
        "category": "cpu",
        "description": "32-core single-socket-only Genoa. Same platform, lower licence footprint.",
        "specs": {
            "socket": "SP5",
            "cores": 32,
            "threads": 64,
            "base_clock_ghz": 3.25,
            "boost_clock_ghz": 3.8,
            "tdp_watts": 280,
            "l3_cache_mb": 256,
            "memory_channels": 12,
        },
        "variants": [
            ("EPYC-9354P-TRAY", {"packaging": "Tray"}, "2980.00", 0),
        ],
    },
    {
        "slug": "micron-ddr5-rdimm-4800",
        "name": "Micron DDR5 RDIMM 4800MT/s",
        "brand": "Micron",
        "category": "ram",
        "description": "Registered ECC DDR5 for Genoa and Sapphire Rapids platforms. Sold per module.",
        "specs": {
            "memory_type": "DDR5 RDIMM",
            "speed_mhz": 4800,
            "latency_cl": "CL40",
            "ecc": "yes",
            "voltage": "1.1V",
            "rank": "2Rx8",
        },
        "variants": [
            ("MICRON-D5-32G-4800", {"capacity_gb": 32}, "129.00", 240),
            ("MICRON-D5-64G-4800", {"capacity_gb": 64}, "249.00", 120),
            ("MICRON-D5-128G-4800", {"capacity_gb": 128}, "689.00", 30),
        ],
    },
    {
        "slug": "samsung-ddr5-rdimm-5600",
        "name": "Samsung DDR5 RDIMM 5600MT/s",
        "brand": "Samsung",
        "category": "ram",
        "description": "5600MT/s registered ECC modules for current-generation dual-socket boards.",
        "specs": {
            "memory_type": "DDR5 RDIMM",
            "speed_mhz": 5600,
            "latency_cl": "CL46",
            "ecc": "yes",
            "voltage": "1.1V",
            "rank": "2Rx4",
        },
        "variants": [
            ("SAMSUNG-D5-64G-5600", {"capacity_gb": 64}, "319.00", 84),
            ("SAMSUNG-D5-96G-5600", {"capacity_gb": 96}, "512.00", 22),
        ],
    },
    {
        "slug": "kingston-server-premier-ddr4-3200",
        "name": "Kingston Server Premier DDR4 3200MT/s",
        "brand": "Kingston",
        "category": "ram",
        "description": "ECC DDR4 for existing Ice Lake and Milan fleets still in service.",
        "specs": {
            "memory_type": "DDR4 RDIMM",
            "speed_mhz": 3200,
            "latency_cl": "CL22",
            "ecc": "yes",
            "voltage": "1.2V",
            "rank": "2Rx8",
        },
        "variants": [
            ("KINGSTON-D4-32G-3200", {"capacity_gb": 32}, "94.00", 310),
            ("KINGSTON-D4-64G-3200", {"capacity_gb": 64}, "179.00", 96),
        ],
    },
]


def seed_categories(db) -> dict[str, Category]:
    by_slug: dict[str, Category] = {}
    for entry in CATEGORIES:
        category = db.query(Category).filter(Category.slug == entry["slug"]).first()
        if category is None:
            category = Category(slug=entry["slug"], name=entry["name"])
            db.add(category)
        category.name = entry["name"]
        by_slug[entry["slug"]] = category
    db.flush()

    for entry in CATEGORIES:
        if entry["parent"]:
            by_slug[entry["slug"]].parent_id = by_slug[entry["parent"]].id
    db.flush()
    return by_slug


def seed_products(db, categories: dict[str, Category]) -> tuple[int, int]:
    created = updated = 0
    for entry in PRODUCTS:
        product = db.query(Product).filter(Product.slug == entry["slug"]).first()
        if product is None:
            product = Product(slug=entry["slug"])
            db.add(product)
            created += 1
        else:
            updated += 1

        product.name = entry["name"]
        product.brand = entry["brand"]
        product.category_id = categories[entry["category"]].id
        product.description = entry["description"]
        product.specs = entry["specs"]
        product.is_published = True
        db.flush()

        existing_variants = {v.sku: v for v in product.variants}
        for sku, attributes, price, stock in entry["variants"]:
            variant = existing_variants.get(sku)
            if variant is None:
                variant = ProductVariant(product_id=product.id, sku=sku)
                product.variants.append(variant)
            variant.attributes = attributes
            variant.price = Decimal(price)
            variant.stock_quantity = stock

        if not product.images:
            url = write_placeholder(entry["category"], entry["slug"], entry["name"])
            product.images.append(
                ProductImage(url=url, alt_text=entry["name"], sort_order=0)
            )

    db.commit()
    return created, updated


def main() -> int:
    db = SessionLocal()
    try:
        categories = seed_categories(db)
        created, updated = seed_products(db, categories)
        db.commit()
        print(f"Categories: {len(categories)}")
        print(f"Products: {created} created, {updated} updated")
    except SQLAlchemyError as exc:
        db.rollback()
        print(f"Seeding failed: {exc}", file=sys.stderr)
        print("Is the database migrated? Run `alembic upgrade head` first.", file=sys.stderr)
        return 1
    finally:
        db.close()

    db = SessionLocal()
    try:
        indexed = reindex_all(db)
        print(f"Elasticsearch: {indexed} documents indexed")
    except Exception as exc:  # noqa: BLE001
        print(f"Elasticsearch unavailable ({exc}).", file=sys.stderr)
        print("Storefront search will use the Postgres fallback until you run "
              "`python -m scripts.seed_demo` again or POST /api/admin/products/reindex.", file=sys.stderr)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
