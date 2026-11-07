# Deploying to ArvanCloud CaaS

Project **tech-store**, namespace
**`340807bbedab0817b72deb2d879d33ac2d508181-tech-store`**.

`techstore.yaml` holds the whole stack and targets that namespace on every
object. ArvanCloud created the namespace, so the manifest does not — and must
not — contain a `Namespace` resource.

## 0. Point kubectl at the namespace

Do this once, after downloading the kubeconfig from the ArvanCloud console.
Every command below then needs no `-n` flag, so none of them can land in the
wrong namespace:

```bash
kubectl config set-context --current \
  --namespace=340807bbedab0817b72deb2d879d33ac2d508181-tech-store

kubectl config view --minify -o jsonpath='{..namespace}'   # confirm
```

## 1. Build and push three images

`NEXT_PUBLIC_API_URL` is inlined into the frontend bundles at build time, so the
public API address must be known before you build — not set later as an env var.

```bash
# backend — from the TechStoreBack repo root
docker build -t <registry>/techstore-backend:v1 .

# frontends — from the TechStoreFront repo root
docker build -t <registry>/techstore-storefront:v1 \
  --build-arg NEXT_PUBLIC_API_URL=https://api.<your-domain> ./storefront

docker build -t <registry>/techstore-admin:v1 \
  --build-arg NEXT_PUBLIC_API_URL=https://api.<your-domain> \
  --build-arg NEXT_PUBLIC_STOREFRONT_URL=https://shop.<your-domain> ./admin

docker push <registry>/techstore-backend:v1
docker push <registry>/techstore-storefront:v1
docker push <registry>/techstore-admin:v1
```

Use a real tag, never `latest` — with `latest` a rolling update may not pull the
new image at all.

## 2. Create the Secret

Not in the manifest, and not in git:

```bash
kubectl create secret generic techstore-secrets \
  --from-literal=POSTGRES_USER=techstore \
  --from-literal=POSTGRES_DB=techstore \
  --from-literal=POSTGRES_PASSWORD="$(openssl rand -base64 32)" \
  --from-literal=JWT_SECRET="$(openssl rand -hex 32)" \
  --from-literal=DATABASE_URL="postgresql://techstore:<the password above>@postgres:5432/techstore" \
  --from-literal=S3_ENDPOINT_URL=https://s3.ir-thr-at1.arvanstorage.ir \
  --from-literal=S3_BUCKET=techstore-media \
  --from-literal=S3_ACCESS_KEY=<key> \
  --from-literal=S3_SECRET_KEY=<secret> \
  --from-literal=S3_PUBLIC_BASE_URL=https://techstore-media.s3.ir-thr-at1.arvanstorage.ir
```

**S3 is not optional at 2 backend replicas.** Uploaded images go to object
storage because a `ReadWriteOnce` volume cannot be shared by two pods. Leave the
`S3_*` values blank only if you also drop the backend to `replicas: 1`, and then
uploads live in the container and vanish on restart.

## 3. Apply, then migrate

```bash
kubectl apply -f techstore.yaml
kubectl wait --for=condition=complete job/migrate --timeout=300s

kubectl exec deploy/backend -- \
  python -m scripts.create_admin --email you@example.com --role admin
```

On a later deploy that adds a migration, re-run the Job:

```bash
kubectl delete job migrate
kubectl apply -f techstore.yaml
```

## 4. Import the catalogue

The workbook is not in the repo. Copy it in and dry-run first:

```bash
kubectl cp ./warehouse.xlsx \
  "$(kubectl get pod -l app=backend -o name | head -1 | cut -d/ -f2)":/tmp/warehouse.xlsx

kubectl exec deploy/backend -- \
  python -m scripts.import_warehouse --file /tmp/warehouse.xlsx --dry-run
```

## What this costs, and where

ArvanCloud enforces **2Gi of RAM per CPU core in limits**, so CPU and memory
cannot be chosen independently. The valid steps are 125m/256Mi, 250m/512Mi,
375m/768Mi, 500m/1024Mi — each container sits on the smallest step that covers
what it actually needs.

| | replicas | request | limit | why |
|---|---|---|---|---|
| backend | 2 | 125m / 256Mi | 250m / 512Mi | measured ~100MB per uvicorn worker under search load |
| storefront | 2 | 125m / 256Mi | 250m / 512Mi | 2 replicas so a deploy or node drain is invisible |
| admin | 1 | 125m / 256Mi | 250m / 512Mi | staff tool; cheapest place to save |
| elasticsearch | 1 | 375m / 768Mi | 500m / 1024Mi | 512MB heap plus JVM overhead; ample for 483 documents |
| postgres | 1 | 250m / 512Mi | 500m / 1024Mi | the stateful pod, so it gets the headroom; plus a 10Gi volume |

**2.5Gi of RAM requests**, 1.25 cores, and 15Gi of persistent storage in total.
The migration Job is transient and not part of the standing bill.

**Elasticsearch is the single most expensive pod and the one real single point
of failure.** A second ES node would roughly double the stack's memory bill, so
this manifest runs one. If it dies, search returns 503 and `/health` reports
`degraded` while product pages, quotes and the admin panel keep working — that
is why the backend's readiness probe deliberately ignores ES. See `DEBT.md`
item 13.

Two things to fix once it is live:

- **`index.number_of_replicas: 0`** on the products index. A single-node cluster
  cannot assign a replica shard, so the index sits permanently `yellow`.
- **Postgres has no backups here.** A `pg_dump` CronJob to object storage is the
  cheap fix; ArvanCloud's managed database is the reliable one. Do one of them
  before real customer quotes land in it.

## Things to check against your own cluster

- `ingressClassName: nginx` and the `nginx.ingress.kubernetes.io/*` annotations
  assume an ingress-nginx controller. Adjust if ArvanCloud's differs.
- `secretName: techstore-tls` assumes cert-manager or a certificate you upload.
  If ArvanCloud terminates TLS at its own edge or CDN instead, drop the `tls:`
  block and point the domains there.
- `volumeClaimTemplates` use the default StorageClass. Name one explicitly if
  the cluster has no default.
