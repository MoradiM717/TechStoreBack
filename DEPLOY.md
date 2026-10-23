# Deploying TechStore

Written for the state the catalogue is in today: 483 products imported from the
warehouse workbook, **no prices**, quote-only buying. Read `DEBT.md` before
promising anyone a feature that isn't here yet.

TechStore is two repositories. This one (**TechStoreBack**) holds the API,
Postgres and Elasticsearch; the storefront and admin panel are in
**TechStoreFront**. The compose files here start the API stack only — deploy the
frontends from their own repo and point them at this API with
`NEXT_PUBLIC_API_URL`. The proxy section below assumes both are on one host; if
they are not, only the `api.example.com` route belongs to this repo.

---

## 1. Before you touch a server

Run the whole thing locally one more time and confirm each of these yourself.
Nothing below takes more than a minute, and each one has bitten a deployment
somewhere.

```bash
docker compose up -d
docker compose exec backend alembic upgrade head
curl -s localhost:8000/health
```

- [ ] `/health` reports `database: ok` and `search: ok`
- [ ] Storefront lists 483 products and the category counts look right
- [ ] A product page shows **Price on request**, not a zero
- [ ] Adding to the quote list and submitting produces an `RFQ-…` reference
- [ ] The request appears in the admin under **Quotes**, marked *new*
- [ ] Signing in as `viewer@…` hides every edit control
- [ ] Persian and Arabic both render right-to-left with the numbers still LTR

---

## 2. Secrets

Generate real ones. The development values are published in this repository and
must not reach a server.

```bash
cp .env.production.example .env.production
openssl rand -base64 32   # POSTGRES_PASSWORD
openssl rand -hex 32      # JWT_SECRET
```

`CORS_ORIGINS` has to list both public front-ends exactly, scheme included —
a mismatch here is the single most common cause of "the admin panel loads but
nothing works".

---

## 3. Build and start

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build
docker compose -f docker-compose.prod.yml --env-file .env.production \
  exec backend alembic upgrade head
```

This brings up Postgres, Elasticsearch and the API. The two frontends are built
and started from the TechStoreFront repository — do that after this stack is
answering on `/health`, because `NEXT_PUBLIC_API_URL` is baked into their images
at build time and a frontend built against the wrong address needs a rebuild,
not a restart.

The production stack binds Postgres and Elasticsearch to the compose network
only, and publishes the three HTTP services on `127.0.0.1`. **Nothing is exposed
to the internet until you put a proxy in front of it.**

---

## 4. TLS and the proxy

Terminate TLS at a reverse proxy (Caddy below; nginx or a cloud load balancer
work the same way) and route three names at the three local ports:

```
shop.example.com   { reverse_proxy 127.0.0.1:3000 }
admin.example.com  { reverse_proxy 127.0.0.1:3001 }
api.example.com    { reverse_proxy 127.0.0.1:8000 }
```

The admin panel has no IP restriction of its own. If it only needs to be
reachable from the office, restrict `admin.example.com` at the proxy — that is
far cheaper than building it into the app.

---

## 5. First run on a fresh server

```bash
# an operator account — there is no signup route, by design
docker compose -f docker-compose.prod.yml --env-file .env.production \
  exec backend python -m scripts.create_admin --email you@example.com --role admin

# the catalogue
docker compose -f docker-compose.prod.yml --env-file .env.production \
  cp "اکسل انبار.xlsx" backend:/app/warehouse.xlsx
docker compose -f docker-compose.prod.yml --env-file .env.production \
  exec backend python -m scripts.import_warehouse --file /app/warehouse.xlsx --dry-run
# read the report, then:
docker compose -f docker-compose.prod.yml --env-file .env.production \
  exec backend python -m scripts.import_warehouse --file /app/warehouse.xlsx --apply
```

**Always `--dry-run` first.** It writes nothing and prints the same report as
`--apply`, including every row it had to guess about.

---

## 6. Re-importing later

The importer is idempotent — it matches on SKU, so a second run updates stock
and specifications instead of duplicating the catalogue. That makes it the
right tool for a routine stock refresh:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production \
  exec backend python -m scripts.import_warehouse --file /app/warehouse.xlsx --apply
```

Two things it will **not** do, by design:

- It never deletes. A part dropped from the spreadsheet stays in the catalogue
  with its last known stock. Unpublish it in the admin panel instead.
- It never touches prices. Prices entered by your team survive every re-import.

---

## 7. Backups

The catalogue can be rebuilt from the spreadsheet; quote requests cannot. Back
up Postgres.

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production \
  exec -T postgres pg_dump -U techstore techstore | gzip > techstore-$(date +%F).sql.gz
```

Elasticsearch needs no backup — it is a derived index. Rebuild it any time:

```bash
curl -X POST https://api.example.com/api/admin/products/reindex \
  -H "Authorization: Bearer <admin token>"
```

---

## 8. After deploying

- [ ] `https://api.example.com/health` returns ok from outside the server
- [ ] Storefront and admin both load over HTTPS with no mixed-content warnings
- [ ] Submit a real quote request and confirm it reaches the admin queue
- [ ] Check `docker compose logs backend` for anything unexpected
- [ ] Set a reminder to check currency rates — nothing refreshes them

---

## Known operational limits

These are properties of the current build, not bugs to file. `DEBT.md` has the
full list with the work each one implies.

| Limit | Consequence |
|---|---|
| No outbound email | Quote requests and enquiries are only visible in the admin panel. Someone has to look. |
| Rate limiting is per-process | `BACKEND_WORKERS=4` means the contact and quote throttles are four times looser than they read. Fine at this volume; move to Redis before it isn't. |
| Images on a local volume | Only one backend container can serve them. Configure `S3_*` before scaling out. |
| Elasticsearch has no auth | It is not published outside the compose network. Keep it that way. |
| Currency rates are manual | A stale rate silently misprices every converted figure. |
