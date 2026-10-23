#!/usr/bin/env python
"""
Import the warehouse workbook (اکسل انبار.xlsx) into the catalogue.

    python -m scripts.import_warehouse --file /path/to/warehouse.xlsx --dry-run
    python -m scripts.import_warehouse --file /path/to/warehouse.xlsx --apply

What it reads
-------------
  "لیست قطعات"  spare parts   — name, part number, quantity, location, note
  "ها ML"       built servers — model, CPU, RAM, controller/PSU

What it does with them
----------------------
Each part becomes a Product with a single purchasable Variant whose SKU is the
part number. Prices are left empty: the workbook has none, so every SKU lands as
sell-on-request until sales fills one in.

Idempotent. Rows are matched on the SKU we derive, so a second run updates stock
and specs rather than duplicating the catalogue. Run `--dry-run` first; it writes
nothing and prints the same report as `--apply`.
"""
import argparse
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.category import Category
from app.models.product import Product, ProductVariant
from app.services.search_service import reindex_all

PARTS_SHEET = "لیست قطعات"
SERVERS_SHEET = "ها ML"

# The workbook's own category list (sheet "لیست‌ها"), which is what the warehouse
# team already agreed on. Slugs are ours; the names are theirs.
CATEGORIES: list[tuple[str, str, str | None]] = [
    ("components", "Components", None),
    ("storage", "Storage", "components"),
    ("hdd", "HDD", "storage"),
    ("ssd", "SSD", "storage"),
    ("nvme", "NVMe", "storage"),
    ("memory", "RAM", "components"),
    ("processors", "CPU", "components"),
    ("raid-controllers", "RAID Controller", "components"),
    ("network-cards", "NIC", "components"),
    ("power-supplies", "Power Supply", "components"),
    ("caddies", "Caddy", "components"),
    ("fans", "Fan", "components"),
    ("motherboards", "Motherboard", "components"),
    ("other-parts", "Other", "components"),
    ("configured-servers", "Configured Servers", None),
]

# Free-text note -> category slug. First match wins, so the order matters:
# "SSD NVMe" has to be tested for NVMe before SSD.
CATEGORY_RULES: list[tuple[str, str]] = [
    (r"\bNVME\b|\bU\.?[23]\b|PM17\d\d", "nvme"),
    (r"\bSSD\b", "ssd"),
    (r"\bHDD\b|\bLTO\b", "hdd"),
    (r"\bRAM\b|\bDIMM\b|\d+GB\s+\dR\b", "memory"),
    (r"\bCPU\b|XEON|\bES\b", "processors"),
    (r"CONTROL|\bRAID\b|EXPANDER|SMART ARRAY|\bHBA\b", "raid-controllers"),
    (r"\bNIC\b|ADAPTER|ETHERNET|\bFC\b|\bGBE\b|QSFP|SFP", "network-cards"),
    (r"POWER|\bPSU\b|\bPWR\b", "power-supplies"),
    (r"CADDY|\bTRAY\b|\bBAY\b", "caddies"),
    (r"\bFAN\b", "fans"),
    (r"BOARD|RISER|BPKIT|\bBEZEL\b", "motherboards"),
]

BRAND_RULES: list[tuple[str, str]] = [
    (r"\bHPE\b", "HPE"), (r"\bHP\b", "HP"), (r"SAMSUNG", "Samsung"),
    (r"SEAGATE", "Seagate"), (r"\bDELL\b", "Dell"), (r"MICRON", "Micron"),
    (r"\bINTEL\b|\bXEON\b", "Intel"), (r"TOSHIBA", "Toshiba"),
    (r"KIOXIA", "Kioxia"), (r"HITACHI|\bHGST\b", "Hitachi"),
    (r"\bSKY?\b|HYNIX", "SK hynix"), (r"\bWD\b|WESTERN", "Western Digital"),
    (r"BROADCOM|\bBCM\b", "Broadcom"), (r"MARVELL|QLOGIC|\bQL\d", "Marvell"),
    (r"MELLANOX|\bMLX\b|CONNECTX", "Mellanox"), (r"MICROCHIP|ADAPTEC", "Microchip"),
    (r"\b3PAR\b|PRIMERA|\bMSA\b", "HPE"),
]

# The note column carries condition as much as category.
CONDITION_RULES: list[tuple[str, str]] = [
    (r"\bES\b", "engineering-sample"),
    (r"RITEL|RETAIL|NORMAL PACK", "new-retail"),
    (r"\bREF\b|REFURB|\bUSED\b", "refurbished"),
]

LOCATION_NAMES = {"انبارa": "Warehouse A", "انبار a": "Warehouse A",
                  "انبارb": "Warehouse B", "انبار b": "Warehouse B"}


def clean(value) -> str:
    """Normalise a cell: collapse whitespace, strip Persian zero-width joiners."""
    if value is None:
        return ""
    text = str(value).replace("‌", " ").replace("‏", "")
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", " ", text).strip()


def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value.lower())
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return re.sub(r"^-+|-+$", "", value)[:200] or "item"


def first_match(rules: list[tuple[str, str]], blob: str, default: str) -> str:
    for pattern, result in rules:
        if re.search(pattern, blob, re.IGNORECASE):
            return result
    return default


def parse_quantity(value) -> tuple[int, str | None]:
    """
    Quantities are mostly integers, but a few cells read like "10 ES" (ten
    engineering samples) and a handful are the header word pasted into the body.
    Returns (quantity, note_about_what_we_found).
    """
    if value is None or clean(value) == "":
        return 0, "blank quantity, treated as 0"
    if isinstance(value, (int, float)):
        return max(int(value), 0), None

    text = clean(value)
    digits = re.search(r"\d+", text)
    if not digits:
        return 0, f"unreadable quantity {text!r}, treated as 0"
    return int(digits.group()), f"quantity {text!r} read as {digits.group()}"


def extract_specs(name: str, note: str) -> dict:
    """Pull the figures a buyer filters on out of the free-text part name."""
    specs: dict[str, str | int | float] = {}
    blob = f"{name} {note}"

    if m := re.search(r"(\d+(?:\.\d+)?)\s*TB", blob, re.IGNORECASE):
        specs["capacity_tb"] = float(m.group(1))
    elif m := re.search(r"(\d+)\s*GB(?!\s*/?s)", blob, re.IGNORECASE):
        specs["capacity_gb"] = int(m.group(1))

    if m := re.search(r"\b(\d{4,5})\s*RPM\b|\b(\d+)K\s*RPM\b|\b(\d+)K\b", blob, re.IGNORECASE):
        rpm = m.group(1) or (m.group(2) or m.group(3))
        if rpm:
            specs["rpm"] = int(rpm) * (1000 if len(rpm) <= 2 else 1)

    if m := re.search(r"\b(\d+)\s*G(?:b|bps)?\b(?!B)", blob):
        specs["interface_gbps"] = int(m.group(1))
    if m := re.search(r"\b(2\.5|3\.5)\s*(?:in|inch|\")?", blob):
        specs["form_factor_in"] = m.group(1)
    if re.search(r"\bSAS\b", blob, re.IGNORECASE):
        specs["interface"] = "SAS"
    elif re.search(r"\bSATA\b", blob, re.IGNORECASE):
        specs["interface"] = "SATA"
    elif re.search(r"\bNVME\b", blob, re.IGNORECASE):
        specs["interface"] = "NVMe"
    if m := re.search(r"\b(\d{3,4})\s*MT/?s\b|\b([2-9]\d{3})\b(?!\s*GB)", blob):
        speed = m.group(1) or m.group(2)
        if speed and 1600 <= int(speed) <= 8800:
            specs["speed_mts"] = int(speed)
    if m := re.search(r"\b(\d)R\b", blob):
        specs["rank"] = f"{m.group(1)}R"
    return specs


def parse_server_row(row) -> dict | None:
    """One row of the ML sheet: model, CPU, RAM, and whatever else was fitted."""
    _, model, cpu, ram, extras = (tuple(row) + (None,) * 5)[:5]
    model = clean(model)
    if not model:
        return None

    specs: dict[str, str | int] = {"model": model}
    if cpu_s := clean(cpu):
        specs["cpu"] = re.sub(r"^CPU\s*", "", cpu_s, flags=re.IGNORECASE)
    if ram_s := clean(ram):
        specs["memory"] = re.sub(r"^RAM\s*", "", ram_s, flags=re.IGNORECASE)
        if m := re.search(r"(\d+)\s*GB", ram_s, re.IGNORECASE):
            count = re.search(r"(\d+)\s*عدد", ram_s)
            modules = int(count.group(1)) if count else 1
            specs["memory_gb_total"] = int(m.group(1)) * modules
        if m := re.search(r"\b([2-9]\d{3})\b", ram_s):
            specs["memory_speed_mts"] = int(m.group(1))
    if extras_s := clean(extras):
        specs["fitted"] = extras_s
        if m := re.search(r"Power\s*(\d+)", extras_s, re.IGNORECASE):
            specs["psu_watts"] = int(m.group(1))
        if m := re.search(r"\b([EP]\d{3}[a-zA-Z\-]*(?:-[A-Z])?|MR\d{3})\b", extras_s):
            specs["storage_controller"] = m.group(1)
    if m := re.search(r"\b(\d+)(SFF|LFF)\b", model, re.IGNORECASE):
        specs["drive_bays"] = int(m.group(1))
        specs["bay_form_factor"] = m.group(2).upper()
    if m := re.search(r"\bG(\d+\+?)\b", model):
        specs["generation"] = f"Gen{m.group(1)}"
    return {"model": model, "specs": specs}


def ensure_categories(db: Session) -> dict[str, Category]:
    by_slug = {c.slug: c for c in db.query(Category).all()}
    for slug, name, parent in CATEGORIES:
        if slug not in by_slug:
            by_slug[slug] = Category(slug=slug, name=name)
            db.add(by_slug[slug])
    db.flush()
    for slug, name, parent in CATEGORIES:
        if parent:
            by_slug[slug].parent_id = by_slug[parent].id
    db.flush()
    return by_slug


def main() -> int:
    parser = argparse.ArgumentParser(description="Import the warehouse workbook.")
    parser.add_argument("--file", required=True, help="path to the .xlsx")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    group.add_argument("--apply", action="store_true", help="write to the database")
    parser.add_argument("--publish", action="store_true", default=True,
                        help="publish imported products (default)")
    parser.add_argument("--draft", dest="publish", action="store_false",
                        help="import everything as unpublished drafts")
    args = parser.parse_args()

    try:
        from openpyxl import load_workbook
    except ImportError:
        print("openpyxl is required: pip install openpyxl", file=sys.stderr)
        return 1

    path = Path(args.file)
    if not path.exists():
        print(f"No such file: {path}", file=sys.stderr)
        return 1

    wb = load_workbook(path, read_only=True, data_only=True)
    missing = [s for s in (PARTS_SHEET, SERVERS_SHEET) if s not in wb.sheetnames]
    if missing:
        print(f"Workbook is missing sheet(s): {missing}", file=sys.stderr)
        print(f"Found: {wb.sheetnames}", file=sys.stderr)
        return 1

    db = SessionLocal()
    report = {"parts": 0, "servers": 0, "created": 0, "updated": 0, "skipped": 0}
    warnings: list[str] = []
    by_category: Counter = Counter()
    seen_skus: dict[str, str] = {}

    try:
        categories = ensure_categories(db)
        existing = {v.sku: v for v in db.query(ProductVariant).all()}

        # ---------------------------------------------------------- parts
        for index, row in enumerate(wb[PARTS_SHEET].iter_rows(min_row=2, values_only=True), start=2):
            name, part_number, qty_raw, location, note = (tuple(row) + (None,) * 5)[:5]
            name, part_number = clean(name), clean(part_number)
            note, location = clean(note), clean(location)
            if not (name or part_number):
                continue

            # The header word turns up pasted into the body a few times.
            if name in {"نام قطعه", "Part Number"} or part_number == "Part Number":
                warnings.append(f"row {index}: looks like a repeated header, skipped")
                report["skipped"] += 1
                continue

            report["parts"] += 1
            blob = f"{note} {name}"
            category_slug = first_match(CATEGORY_RULES, blob, "other-parts")
            brand = first_match(BRAND_RULES, blob, "Unbranded")
            condition = first_match(CONDITION_RULES, blob, "new")
            quantity, qty_note = parse_quantity(qty_raw)
            if qty_note:
                warnings.append(f"row {index} ({part_number or name[:28]}): {qty_note}")

            # Parts with no number still need a stable SKU, so derive one from the
            # name. It is marked so staff can tell it apart from a real HPE number.
            if part_number:
                sku = part_number.upper()
            else:
                sku = f"NOPN-{slugify(name)[:40].upper()}"
                warnings.append(f"row {index}: no part number, SKU generated as {sku}")

            if sku in seen_skus:
                warnings.append(
                    f"row {index}: SKU {sku} already seen ({seen_skus[sku][:30]}); "
                    "quantities merged"
                )
                if args.apply and (variant := existing.get(sku)):
                    variant.stock_quantity += quantity
                continue
            seen_skus[sku] = name or sku

            display_name = name or part_number
            specs = extract_specs(name, note)
            specs["part_number"] = part_number or "—"
            if location:
                specs["warehouse"] = LOCATION_NAMES.get(location.lower(), location)
            if note:
                specs["source_note"] = note

            by_category[category_slug] += 1
            if not args.apply:
                continue

            variant = existing.get(sku)
            if variant is None:
                product = Product(
                    name=display_name[:255],
                    slug=slugify(f"{display_name}-{sku}")[:280],
                    brand=brand,
                    category_id=categories[category_slug].id,
                    description=None,
                    specs=specs,
                    is_published=args.publish,
                )
                variant = ProductVariant(
                    sku=sku,
                    attributes={"condition": condition,
                                "warehouse": specs.get("warehouse", "—")},
                    price=None,          # no prices in the workbook: sold on request
                    currency="IRR",
                    condition=condition,
                    stock_quantity=quantity,
                )
                product.variants.append(variant)
                db.add(product)
                existing[sku] = variant
                report["created"] += 1
            else:
                product = variant.product
                product.name = display_name[:255]
                product.brand = brand
                product.category_id = categories[category_slug].id
                product.specs = specs
                product.is_published = args.publish
                variant.stock_quantity = quantity
                variant.condition = condition
                variant.attributes = {"condition": condition,
                                      "warehouse": specs.get("warehouse", "—")}
                report["updated"] += 1

        # -------------------------------------------------------- servers
        for index, row in enumerate(wb[SERVERS_SHEET].iter_rows(min_row=2, values_only=True), start=2):
            parsed = parse_server_row(row)
            if not parsed:
                continue
            report["servers"] += 1
            model, specs = parsed["model"], parsed["specs"]

            # Built servers have no part number; the row number keeps the SKU stable
            # across re-imports as long as the sheet isn't reordered.
            sku = f"SRV-{index - 1:03d}-{slugify(model)[:28].upper()}"
            if sku in seen_skus:
                continue
            seen_skus[sku] = model
            by_category["configured-servers"] += 1
            if not args.apply:
                continue

            brand = first_match(BRAND_RULES, model, "HPE")
            variant = existing.get(sku)
            if variant is None:
                product = Product(
                    name=f"HPE ProLiant {model}"[:255],
                    slug=slugify(f"{model}-{index - 1}")[:280],
                    brand=brand,
                    category_id=categories["configured-servers"].id,
                    description=None,
                    specs=specs,
                    is_published=args.publish,
                )
                variant = ProductVariant(
                    sku=sku,
                    attributes={"configuration": specs.get("cpu", ""),
                                "memory": specs.get("memory", "")},
                    price=None,
                    currency="IRR",
                    condition="new",
                    stock_quantity=1,   # each row is one physical machine
                )
                product.variants.append(variant)
                db.add(product)
                existing[sku] = variant
                report["created"] += 1
            else:
                variant.product.specs = specs
                variant.product.is_published = args.publish
                report["updated"] += 1

        if args.apply:
            db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        print(f"Import failed, nothing was written: {exc}", file=sys.stderr)
        raise
    finally:
        db.close()

    # ------------------------------------------------------------ report
    mode = "DRY RUN — nothing written" if not args.apply else "APPLIED"
    print(f"\n{'=' * 64}\n{mode}\n{'=' * 64}")
    print(f"  parts read      {report['parts']}")
    print(f"  servers read    {report['servers']}")
    print(f"  products created {report['created']}")
    print(f"  products updated {report['updated']}")
    print(f"  rows skipped     {report['skipped']}")
    print(f"\n  by category:")
    for slug, count in by_category.most_common():
        print(f"    {count:>5}  {slug}")

    print(f"\n  warnings ({len(warnings)}):")
    for w in warnings[:40]:
        print(f"    - {w}")
    if len(warnings) > 40:
        print(f"    … and {len(warnings) - 40} more")

    if args.apply:
        print("\n  reindexing search…")
        db = SessionLocal()
        try:
            print(f"  indexed {reindex_all(db)} published products")
        except Exception as exc:  # noqa: BLE001
            print(f"  search index not updated ({exc}); run the reindex endpoint later")
        finally:
            db.close()

    print(f"\n  NOTE: no prices were set — the workbook has none. Every SKU is")
    print(f"  sell-on-request until prices are entered in the admin panel.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
