# Deploying TechStore on Hamravesh (Darkube)

Darkube is Hamravesh's Kubernetes PaaS. This is the plan for *this* repository —
five pieces, three of them built from this monorepo.

`DEPLOY.md` covers the generic Docker Compose deployment. This file replaces it
if you host on Hamravesh.

---

## What goes where

| Piece | How | Why |
|---|---|---|
| **PostgreSQL** | Managed database (DBaaS) | Backups and upkeep included. Don't run a database container you have to look after. |
| **Elasticsearch** | Darkube app → Databases → Elasticsearch | Same namespace as the backend, reached by app name. |
| **Backend** | Darkube app → Git repo (**TechStoreBack**) | Dockerfile at the repo root. |
| **Storefront** | Darkube app → Git repo (**TechStoreFront**) | Dockerfile in `storefront/`. |
| **Admin** | Darkube app → Git repo (**TechStoreFront**) | Dockerfile in `admin/`. |
| **Product images** | S3 bucket | The backend already speaks S3 (`S3_*`). See the disk warning below. |

Put all five in **one namespace** — apps there reach each other by app name,
which is how the backend finds Elasticsearch.

> **Don't import `docker-compose.prod.yml`.** Darkube's Compose import ignores
> `build:`, and all three of our services are built from source. Create them as
> Git-repo apps instead.

---

## 1. Managed PostgreSQL

Create a managed PostgreSQL database in the console. Copy the connection details
from **General Information** — the host looks like `<uuid>.hsvc.ir` on a
non-standard port, not 5432.

If the database sits outside the cluster, enable **public access**
(دسترسی عمومی) or the backend can't reach it.

```
DATABASE_URL=postgresql://postgres:<password>@<uuid>.hsvc.ir:<port>/techstore
```

Create the `techstore` database (or use `postgres` and adjust the URL).

---

## 2. Elasticsearch

Create App → **Databases → Elasticsearch**, in the same namespace. Note the
generated password: user is `elastic`, password is in the app's general
information tab.

Darkube does not publish which version it provisions, and you do not need to
chase it: our pinned `elasticsearch==8.15.1` client works against 8.x **and**
9.x servers. Confirm what you got once it's up:

```bash
curl -u elastic:<password> https://<es-app>.darkube.app | grep number
```

Our backend currently builds its Elasticsearch client from a URL with no
credentials, so put them in the URL:

```
ELASTICSEARCH_URL=http://elastic:<password>@<es-app-name>:9200
```

Use the **app name**, not the `.darkube.app` address — the backend is in the
same namespace, so internal is faster and never leaves the cluster.

---

## 3. Backend app

Create App → **Git repo**, pick **this** repository (TechStoreBack) and branch.

| Field | Value |
|---|---|
| Dockerfile path | `./Dockerfile` |
| Build context | `.` |
| Port name | `http` ← **required**, or you cannot attach a domain |
| Container port | `8000` |

**Environment variables:**

```
DATABASE_URL=postgresql://postgres:<password>@<uuid>.hsvc.ir:<port>/techstore
ELASTICSEARCH_URL=http://elastic:<password>@<es-app-name>:9200
JWT_SECRET=<openssl rand -hex 32>
ACCESS_TOKEN_EXPIRE_MINUTES=480
CORS_ORIGINS=https://shop.example.com,https://admin.example.com
PUBLIC_BASE_URL=https://api.example.com
S3_ENDPOINT_URL=<from the console>
S3_BUCKET=techstore-media
S3_ACCESS_KEY=<scoped key>
S3_SECRET_KEY=<scoped secret>
S3_PUBLIC_BASE_URL=https://<your-s3-domain>/techstore-media
```

The Dockerfile's default command does not start a server with workers. Override
the command in the app's general settings:

```
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4 --proxy-headers --forwarded-allow-ips=*
```

### Media: use S3, not a disk

A Darkube disk **cannot be attached to an app with more than one replica**, and
adding one **disables zero-downtime updates**. Uploaded product images on a disk
would pin the backend to a single replica forever.

Set the `S3_*` variables above and the backend writes to object storage instead
— that code path already exists and is chosen automatically when the keys are
present. Create the bucket, make it public (images are public anyway), and
create a key scoped to just that bucket.

---

## 4. Storefront and admin apps

Two more Git-repo apps, both pointed at the **TechStoreFront** repository (the
two frontends share one repo, so each app differs only in path and port):

| | Storefront | Admin |
|---|---|---|
| Dockerfile path | `./storefront/Dockerfile` | `./admin/Dockerfile` |
| Build context | `./storefront` | `./admin` |
| Container port | `3000` | `3001` |
| Port name | `http` | `http` |

### The one thing that will catch you

`NEXT_PUBLIC_API_URL` is **inlined at build time** by Next.js. It must be set as
a **build argument** in the CI/CD tab — not as an environment variable. As an
env var it does nothing, and the built image calls `http://localhost:8000`,
which fails for every visitor.

```
NEXT_PUBLIC_API_URL = https://api.example.com
```

It must be the **public** API address. The browser makes these calls, so an
in-cluster name like `techstore-backend:8000` will not resolve.

Both Dockerfiles already declare `ARG NEXT_PUBLIC_API_URL`, so nothing in the
repo needs changing.

---

## 5. Domains

### Getting one in the first place

Hamravesh does not sell domains — you connect one you own. You do not need one
to go live: every app gets a free `*.darkube.app` subdomain with automatic SSL,
which is enough to test the whole stack end to end.

**`.ir`** — registered through **IRNIC (nic.ir)**. You create an IRNIC handle
first, then register under it, either directly or through a domestic reseller.
It is cheap and it sits outside US jurisdiction, so no foreign registrar can be
ordered to disable it. US and UK registrars have been barred from selling `.ir`
since 2015, which is why you buy it domestically.

**`.com` / `.net`** — these sit with US registrars under US jurisdiction. For an
Iranian business that is a real, documented risk rather than a theoretical one:
Fars News's `.com` was disabled after its registrar received an OFAC order, and
Abr Arvan's `.com` was put into HOLD at GoDaddy. Press TV moved to `.ir` after
losing its domain.

Given this store quotes in IRR, USD and AED, the sensible shape is:

- **`.ir` as the domain of record** — it cannot be taken by a foreign registrar
- **a `.com` as well if you want international reach**, bought through a domestic
  reseller, treated as useful but not guaranteed
- DNS for both at **ArvanCloud**, so a single switch moves traffic if the `.com`
  ever goes dark

### Pointing it at Darkube

Darkube needs your domain's **nameservers** at an authoritative provider — its
docs name **Cloudflare or ArvanCloud**. ArvanCloud is the domestic choice and
removes any question of a foreign provider dropping the account.

| App | Domain |
|---|---|
| Storefront | `shop.example.com` |
| Admin | `admin.example.com` |
| Backend | `api.example.com` |

For each: name a port `http`, add the domain, enable **SSL** and **HTTPS
redirect**. Point a **CNAME/ANAME** at the app's `.darkube.app` subdomain.

- **Cloudflare** — proxy **off** (grey cloud), SSL/TLS flexible or off.
- **Arvan** — CDN off or origin protocol HTTP, firewall off.

`CORS_ORIGINS` on the backend must list the storefront and admin domains exactly,
scheme included. A mismatch here is the usual cause of "the admin loads but
nothing works".

**Restrict the admin.** Darkube offers HTTP basic auth across an app's domains;
if the admin only needs to be reachable from the office, turn it on.

---

## 6. First run

From the backend app's **web terminal**:

```bash
alembic upgrade head
python -m scripts.create_admin --email you@example.com --role admin
```

Then upload the warehouse workbook and import it. The terminal has no file
upload, so either commit the workbook temporarily, fetch it from S3, or paste it
in via the file-browser plugin:

```bash
python -m scripts.import_warehouse --file /app/warehouse.xlsx --dry-run   # read the report
python -m scripts.import_warehouse --file /app/warehouse.xlsx --apply
```

Confirm the catalogue: `https://api.example.com/health` should report
`database: ok` and `search: ok`, and the storefront should list 483 products.

---

## 7. Deploys after the first

Leave **auto-deploy on push** enabled on all three apps and a push to the branch
rebuilds and rolls out.

For a pipeline elsewhere, trigger it over the API:

```
PUT https://api.console.hamravesh.ir/api/v1/darkube/apps/update_from_cli/
{ "trigger_deploy_token": "…", "app_id": "…", "image_tag": "…" }
```

Token and app ID are on each app's details page.

---

## Order of operations

Dependencies run downhill, so build from the bottom up:

1. Managed PostgreSQL — get the connection string
2. S3 bucket and scoped key
3. Elasticsearch app — get the `elastic` password
4. Backend app — needs 1, 2 and 3; attach `api.example.com`
5. Storefront and admin — need the backend's **public** URL as a build arg
6. Migrate, create an admin user, import the catalogue
7. Set `CORS_ORIGINS` once the front-end domains are final, and redeploy the backend

---

## Things the docs don't answer

Check these in the console rather than assuming:

- The S3 API endpoint hostname and the container registry hostname — both are
  per-organization.
- Whether managed PostgreSQL requires SSL. If it does, append `?sslmode=require`
  to `DATABASE_URL`.
- Which Elasticsearch version is provisioned — **though this matters less than
  it looks.** Our client is `elasticsearch==8.15.1`, and Elastic's own
  compatibility table says an 8.x client supports both 8.x and 9.x servers (it
  negotiates with a `compatible-with=8` header). So whatever Darkube gives you
  in the 8 or 9 line will work. Only a 10.x server would break it, which does
  not exist yet.
- Plan sizing. Elasticsearch wants ~1GB heap minimum for this catalogue.
