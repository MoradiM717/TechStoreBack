# TechStore — API

FastAPI service behind the TechStore hardware catalogue: REST API, database
models, admin auth, and the Elasticsearch search index.

The storefront and admin panel live in a separate repository,
**[TechStoreFront](https://github.com/MoradiM717/TechStoreFront)**. They talk to
this service over HTTP and share nothing but the API contract.

```
app/models/      SQLAlchemy models — products, variants, categories, orders,
                 quotes, currencies, admin users, contact messages
app/routers/     Endpoints, split storefront (public) vs admin (JWT)
app/schemas/     Pydantic request/response models
app/services/    search_service.py — the Elasticsearch index and queries
app/core/        config, database, security, i18n
alembic/         Migrations
scripts/         Admin creation, demo seed, warehouse import, catalogue reset
```

## Catalogue and pricing

The catalogue is imported from the store's warehouse workbook — 483 products
across 12 categories, including 25 fully-configured servers.

**Nothing has a price.** The workbook tracks stock, not commercial terms, so
every SKU is sold on request: the buy path is a quote request, not a checkout.
`product_variants.price` is nullable and the admin form accepts prices whenever
you start entering them, so the catalogue can be priced gradually. A product
with a price shows it; the rest stay on request. See `DEBT.md`.

Three currencies are live — **IRR (base), USD, AED**. Rates are entered by hand
in the admin panel rather than fetched, so a figure quoted to a customer holds
still for the life of the quote. A price entered directly against a currency
always wins over a converted one.

## Running locally

```bash
# 1. Start Postgres, Elasticsearch and the API
cp .env.example .env
docker compose up -d

# 2. Run migrations
docker compose exec backend alembic upgrade head

# 3. Create your first admin user (there is no signup route, on purpose)
docker compose exec backend python -m scripts.create_admin \
  --email you@example.com --role admin

# 4. Optional: seed categories and a demo catalogue
docker compose exec backend python -m scripts.seed_demo
```

The API is then on `http://localhost:8000`, with interactive docs at `/docs`.
Postgres is published on host port **5433** (5432 is commonly already taken);
inside the compose network it is still 5432, and compose overrides
`DATABASE_URL` and `ELASTICSEARCH_URL` with the in-network service names.

`GET /health` reports whether Postgres and Elasticsearch are actually
reachable, not just whether the process is up.

To run without Docker — the `.env` defaults already point at `localhost`:

```bash
pip install -r requirements.txt
alembic upgrade head && uvicorn app.main:app --reload
```

### Importing the warehouse workbook

The workbook is gitignored: it is business data, not code. Mount or copy it in,
then always dry-run first — it writes nothing and prints the same report.

```bash
docker compose exec backend python -m scripts.import_warehouse \
  --file /app/warehouse.xlsx --dry-run

docker compose exec backend python -m scripts.import_warehouse \
  --file /app/warehouse.xlsx --apply
```

Idempotent on SKU, so a re-run updates stock and specifications rather than
duplicating the catalogue. It never deletes and never touches prices, which
makes it safe as a routine stock refresh.

To clear the catalogue before a fresh import:

```bash
docker compose exec backend python -m scripts.reset_catalogue          # report only
docker compose exec backend python -m scripts.reset_catalogue --yes --all
```

## Search

**Elasticsearch is the only read model for search.** Postgres is the source of
truth and is never read back out of ES; every admin create/update/delete pushes
a fresh denormalized document (brand, category name, price range, flattened
specs) so the storefront's facets and filters resolve in one query instead of
joining per request.

Free text runs two passes: a strict `cross_fields` AND, so a nonsense query
matches nothing, then a per-term fuzzy pass so a one-character slip still lands.
Results carry `relaxed: true` when they came from the second pass.

There is deliberately **no Postgres query path** behind search. One existed and
was removed: it was a second implementation of "what does this query return",
and it gave different answers from the primary one — typo queries found nothing,
and two-word queries like `Samsung 16GB` returned 0 against ES's 2. If the
cluster is unreachable, search raises `SearchUnavailableError`, which surfaces
as a **503** and marks `/health` degraded, rather than quietly answering from a
weaker engine. That makes ES a hard dependency; `DEBT.md` item 13 covers what
that costs and how to run it safely.

`POST /api/admin/products/reindex` rebuilds the index from Postgres. It is
manual recovery — nothing automatically detects a stale index.

## Roles

| | admin | editor | viewer |
|---|---|---|---|
| View products, categories, orders, quotes, messages | ✓ | ✓ | ✓ |
| Create/edit products and categories | ✓ | ✓ | |
| Change order, quote and message status | ✓ | ✓ | |
| Edit currency rates | ✓ | ✓ | |
| Upload images | ✓ | ✓ | |
| Delete products/categories/messages, reindex | ✓ | | |

Enforced by `require_role(...)` on every write endpoint. The admin UI mirrors
this so viewers are not shown buttons that would 403.

## API surface

**Storefront (public)**

| Method | Path | Notes |
|---|---|---|
| GET | `/api/products/search` | Faceted: `q`, `category`, repeatable `brand`, `min_price`, `max_price`, `in_stock`, `spec_<key>`, `sort`, `page`. **503** if ES is down |
| GET | `/api/products/suggest` | Search-as-you-type. Returns an empty list if ES is down |
| GET | `/api/products/{slug}` | Product detail (published only) |
| GET | `/api/products/{slug}/related` | Same-category products |
| GET | `/api/products/variants/{id}/stock` | Live price/stock |
| GET | `/api/categories` | Category list with product counts |
| POST | `/api/contact` | Contact enquiry (rate-limited, stored for the admin inbox) |
| POST | `/api/quotes` | Quote request. Lines may name a SKU we stock or a part number we don't |
| GET | `/api/quotes/{reference}` | Quote lookup by its public `RFQ-` reference |
| GET | `/api/currencies` | Active currencies and their rates |
| POST | `/api/orders` | Create an order from a cart (see below) |
| GET | `/api/orders/{reference}` | Order lookup by its public reference |

**Admin (JWT required)** — `/api/admin/auth/login`, `/me`; full CRUD under
`/api/admin/products` and `/api/admin/categories`; `/api/admin/orders` with
`/{id}/transitions` and `/{id}/status`; `/api/admin/quotes`;
`/api/admin/currencies`; `/api/admin/contact`; `/api/admin/uploads/images`; and
`POST /api/admin/products/reindex`.

Every error response is `{"detail": "<one sentence a human can read>"}`,
including 422s — both frontends display `detail` verbatim, so the wording is
part of the UI.

Shopper-facing messages are translated (en/fa/ar) from the `Accept-Language`
header. Admin API errors stay English; it is a staff tool.

### Order creation

`POST /api/orders` takes variant ids and quantities plus shipping details. It
deliberately does **not** take prices: the server re-reads each variant's
current price from Postgres, locks the rows for the duration, rejects the whole
order if anything is short of stock (naming what is short by how much),
decrements stock, and returns a non-enumerable reference like `TS-A88337CF62F3`.
Guests get a `Customer` row with no credentials.

Order status follows a validated state machine
(`pending → paid → processing → shipped → delivered`, with
`cancelled`/`refunded` exits). Cancelling or refunding returns reserved stock to
inventory.

### Quote requests

A quote is modelled separately from an order rather than overloading it: it has
no payment, its line items may reference a part number that is not in the
catalogue at all (`variant_id` is nullable), and it has its own status machine
guarded by `QUOTE_STATUS_TRANSITIONS`. References are `RFQ-<10 hex>` so a
customer can read one back over the phone.

## Deliberately stubbed

Not implemented, and the API does not pretend otherwise:

- **No payment gateway.** Quote-only; you invoice.
- **No outbound email.** A quote request reaches the admin queue and nothing is
  sent to the customer. This is `DEBT.md` P0 item 2.
- **No customer accounts.** Guest-only; a reference tracks the order or quote.

## Configuration

Settings come from `.env` (see `.env.example`). The repository's dev values —
including `JWT_SECRET=dev-only-change-me` — must never reach a server;
`DEPLOY.md` generates real ones.

Real env files are gitignored. Only `*.example` is tracked.

## Deployment

- `DEPLOY.md` — self-hosted with `docker-compose.prod.yml`, TLS, backups.
- `HAMRAVESH.md` — Hamravesh/Darkube specifically: managed Postgres, ES as an
  app, S3 for uploads, domains.
- `DEBT.md` — 13 known gaps at P0/P1/P2, each with what it costs and what to do,
  plus a "Not debt — decisions" section so deliberate choices are not
  re-litigated as oversights.

Read `DEBT.md` before promising anyone a feature. The P0s are honest: there are
no prices in the catalogue yet, and nothing is emailed on a quote request.
