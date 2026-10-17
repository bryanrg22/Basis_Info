# Staging environment (GCP project `cost-seg-basis`)

The Basis backend's staging environment: what runs where, why, what it costs, how it is
deployed, and how to operate it. Infrastructure as code is in [`infra/staging`](../../infra/staging)
(Terraform) and the deploy job is `deploy-staging` in [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml).

| | |
|---|---|
| API base URL | `https://staging-basis-api-930581733841.us-central1.run.app` (Terraform output `api_url`) |
| Health | `GET <api>/health` (build sha, database and Temporal checks) |
| API docs | `GET <api>/docs` (Swagger UI), `GET <api>/openapi.json` |
| Region | `us-central1` (everything except the optional SAM 3 GPU service, `us-east4`) |
| Deploys | every merge to `main`, after CI passes, in the GitHub environment `staging` |

Prices below are Google's list prices for us-central1 read from the Cloud Billing Catalog
API on 2026-10-17 (no discounts; the YC credits pay them), unless stated otherwise.

## 1. Architecture

```
                      browser (frontend, Firebase Auth)
                         |  HTTPS + Firebase ID token          signed PUT (V4, 15 min)
                         v                                            |
  +---------------------------------------------+                     v
  | Cloud Run service  staging-basis-api        |   +------------------------------------+
  |   uvicorn basis.api.main:app, 0..4 instances|-->| GCS  staging-cost-seg-basis-uploads|
  +-------------+---------------+---------------+   +------------------------------------+
                |               |                                 ^
     Cloud SQL socket   Direct VPC egress (10.20.0.0/24)          | read photos, write outputs
                |               |                                 |
                v               v                                 |
  +-----------------------+   +-------------------------+   +---------------------------------+
  | Cloud SQL Postgres 16 |   | VM staging-temporal     |<--| Cloud Run worker pool           |
  | staging-basis-pg      |<--| Temporal 1.31 + its own |   | staging-basis-worker (1 inst.)  |
  | pgvector, g1-small    |   | Postgres + UI, e2-medium|   |   worker: python -m basis.worker|
  +-----------------------+   | no public IP            |   |   relay:  python -m basis.relay |
                ^             +-------------------------+   +--+------------------+-----------+
                |                                              |                  |
                +----------------------------------------------+   /mnt/private (read-only)
                  Cloud Run jobs: staging-basis-migrate,            GCS staging-cost-seg-basis-private
                  staging-basis-ingest                              (corpus PDFs, cost table)

  Worker -> OpenAI, Anthropic, Azure Document Intelligence, Replicate (SAM 3) over the internet,
  keys from Secret Manager.
```

### Region

`us-central1`, where the Artifact Registry repository already is, a Tier 1 Cloud Run
price region, and next to the model providers' US endpoints. The project has no L4 GPU
quota there, which only matters for self-hosted SAM 3 (section 1.9).

### 1.1 API: Cloud Run service

`staging-basis-api` runs the image's default command (`uvicorn basis.api.main:app` on
port 8000), 1 vCPU and 1 GiB per instance, 40 concurrent requests per instance, 0 to 4
instances, request-based billing (CPU only while a request runs) with startup CPU boost.

* **Why Cloud Run:** a stateless HTTP API with bursty, low traffic; scales to zero, so
  staging's API costs nothing idle. It is the standard target for a containerized API on
  GCP; GKE would add node pools to pay for while idle and a cluster to operate, for no gain
  at this size.
* **Public ingress, application auth.** `allUsers` may invoke it; every `/v1` route
  verifies a Firebase ID token itself (`basis/auth/firebase.py`), and `/health` and the
  OpenAPI document are meant to be open. Cloud Run IAM auth in front would make the
  browser send Google identity tokens, which a Firebase-authenticated frontend does not
  have.
* **Max 4 instances** bounds the database connections: 4 x `DB_POOL_SIZE` 5 = 20 of the
  instance's 50. Raise both together (section 1.3).
* **Startup probe:** TCP on 8000. `/health` is not used as the probe because it answers
  503 when the database is down, and a probe that fails on a dependency outage turns it
  into a restart loop.

### 1.2 Worker and outbox relay: one Cloud Run worker pool

`staging-basis-worker` is a [Cloud Run worker pool](https://cloud.google.com/run/docs/deploy-worker-pools)
with one instance and two containers from the same image:

* `worker` (`python -m basis.worker`), 1 vCPU, 2 GiB: polls the Temporal task queue and
  runs the activities. Startup probe `GET :8080/health`, which turns 200 once it is
  connected to Temporal and polling.
* `relay` (`python -m basis.relay`, sidecar), 1 vCPU, 512 MiB: delivers recorded decisions
  (outbox rows) to the workflow. Startup probe `GET :8081/health`. `DB_POOL_SIZE=2`.

Why this shape:

* **A worker needs CPU while it has no HTTP request.** It long-polls Temporal. A Cloud Run
  *service* with `min-instances=1` and request-based billing throttles CPU between
  requests, so polling stalls; with CPU always allocated it bills the instance-based
  service rate, $0.000018 per vCPU-second. A worker pool is built for pull workloads, has
  no ingress at all, and bills $0.00001124 per vCPU-second and $0.00000124 per GiB-second,
  about 38% less than the same instance as an always-on service.
* **Min instances = 1, and why not 0.** Temporal holds work in the task queue while no
  worker polls, but nothing makes progress: a started study would wait for a worker.
  Worker pools do not autoscale (the instance count is set), so 1 is the floor for a
  usable staging. Parking staging (section 5.4) sets it to 0 when nobody is using it.
* **Why not a Cloud Run job:** a job runs to completion; a worker runs forever. Jobs are
  right for the migrations and the corpus ingest, which do run to completion (section 1.8).
* **The relay as a sidecar,** not its own pool: it is a small poll loop, its outbox read
  uses `FOR UPDATE SKIP LOCKED` (several relays are safe if the pool grows), and a second
  pool would bill a second instance. It scales with the worker, which is what it serves.
* **Scaling out:** set `worker_instances` (Terraform) above 1; each instance adds one
  worker and one relay. Activity concurrency per worker is `PHOTO_CONCURRENCY` (5) and
  `LLM_CONCURRENCY` (4).

### 1.3 Database: Cloud SQL for PostgreSQL 16, Enterprise, db-g1-small

`staging-basis-pg`: PostgreSQL 16 (pgvector is a supported Cloud SQL extension; migration
`0001` runs `CREATE EXTENSION vector`), shared-core `db-g1-small` (1.7 GB RAM), one zone,
10 GB SSD with automatic growth, daily backups kept 7 days, no point-in-time recovery,
Query Insights on, deletion protection on.

| Tier | RAM | default `max_connections` | Price |
|---|---|---|---|
| db-f1-micro | 0.6 GB | 25 | $0.0105/h = $7.67/month |
| **db-g1-small** | 1.7 GB | 50 | **$0.035/h = $25.55/month** |
| db-custom-1-3840 | 3.75 GB | 100 | $0.0413 + 3.75 x $0.007 = $0.0676/h = $49.31/month |

* **db-g1-small, not db-f1-micro:** at full scale-out staging opens 20 (API) + 5 (worker)
  + 2 (relay) + 5 (a migration or ingest job) = 32 connections; db-f1-micro's 25 would
  refuse connections under the load tests, and 0.6 GB is tight for pgvector's HNSW index
  build over 7,466 chunks. Shared-core tiers carry no SLA, acceptable for staging.
  ([max_connections defaults](https://cloud.google.com/sql/docs/postgres/flags#postgres-m))
* **Enterprise, not Enterprise Plus:** Enterprise Plus has no shared-core tier, so its
  smallest machine is a dedicated-core one at several times the price.
* **Alternatives considered:** AlloyDB (priced for production, no shared-core tier); Postgres
  on the Temporal VM (cheapest, but backups, upgrades and HA become ours, and the app's
  data would share a VM with Temporal); an external serverless Postgres (Neon,
  Supabase): another vendor and network path for no saving the YC credits do not cover.
* **Connections:** public IP with no authorized networks, so the only way in is the Cloud
  SQL connector. Cloud Run's built-in connector mounts a Unix socket at
  `/cloudsql/<connection name>`, authorized by the service account's
  `roles/cloudsql.client` and encrypted; the app connects with asyncpg over that socket
  (`postgresql+asyncpg://basis:<password>@/basis?host=/cloudsql/cost-seg-basis:us-central1:staging-basis-pg`,
  verified against the image). `ssl_mode=ENCRYPTED_ONLY` refuses unencrypted TCP.
* **Credentials:** a built-in user `basis` with a random 48-hex-character password,
  generated by `scripts/set-db-password.sh` and written straight into Secret Manager as
  the whole `DATABASE_URL` (`staging-database-url`); Terraform never sees it. IAM database
  authentication (no password) needs the Cloud SQL Python Connector in the app
  (`CLOUD_SQL_INSTANCE` is reserved for it, `basis/db/engine.py`), a production follow-up.

### 1.4 Temporal: self-hosted on one VM now, Temporal Cloud recommended next

| | Self-hosted (deployed) | Temporal Cloud |
|---|---|---|
| Fixed cost | e2-medium $24.46 + disks $4.00 + Cloud NAT about $4.67 = **about $33/month** | **$0 base fee** (pay as you go) |
| Per study | $0 marginal | Actions at $50 per million (first 5M); a study is an estimated few thousand actions (activities, timers, signals, heartbeats), so roughly $0.10 to $0.30: to be measured |
| Credits | YC GCP credits | $150 on signup (90 days); startup program: $6,000 for startups under $30M raised |
| Operations | ours: upgrades (one minor at a time, schema migration), backups (daily disk snapshots), no HA, no TLS inside the VPC | none; mTLS or API keys, multi-AZ, SLA |
| Setup | done (Terraform) | Bryan creates the account and a namespace (cannot be done for him) |

Sources: [Temporal pricing](https://temporal.io/pricing) (read 2026-10-17: "no base
monthly fee", $50 per million actions for the first 5M, $0.042/GB-hour active storage,
$150 credits, $6,000 startup credits); Compute Engine and Cloud NAT list prices above.

**Recommendation:** move staging to Temporal Cloud once the account exists (startup
credits make it free for a long time, and it removes the only VM we operate), then do the
same for production. The self-hosted VM exists because no Temporal Cloud account does
yet and staging is needed now; it is the documented fallback of the 2026-10-15 decisions.

What the VM runs (`infra/staging/temporal/compose.yaml`, started by `startup.sh`):

* Debian 12, e2-medium (1 vCPU sustained, 4 GB), no external IP; Docker Engine from
  Docker's apt repository; container logs to Cloud Logging (`gcplogs` driver).
* `postgres:16.15` as Temporal's persistence and visibility store (not the app's Cloud
  SQL instance: Temporal's own connection pools, 20 per store, would compete with the
  app's 50), on a separate 20 GB data disk with daily snapshots kept 7 days.
* `temporalio/admin-tools:1.31.4` creates both databases and applies schema versions on
  every boot (idempotent; this is how upgrades migrate). `temporalio/auto-setup` is
  deprecated since 1.30, so the explicit schema job replaces it.
* `temporalio/server:1.31.4`, all roles in one process, 8 history shards (fixed at
  creation; each study is a few workflow tasks a minute, its time goes to model calls).
* A one-shot job creates the namespace `basis-staging` (7-day retention); the worker
  registers its search attributes (`StudyId`, `OrgId`, `Stage`) at startup.
* `temporalio/ui:2.54.1` on the VM's localhost only (section 5.2 for the tunnel).
* The database password is generated on the VM at first boot, kept on the data disk,
  and never leaves it.

Verified locally before deploying: the stack starts clean, re-running `up` is a no-op for
schema and namespace, and the Basis client registers its search attributes against it.

**Switching to Temporal Cloud** (about 15 minutes, no code change: `basis/temporal/client.py`
already uses an API key with TLS when `TEMPORAL_API_KEY` is set):

1. Create the namespace (for example `basis-staging.<account>`) in `aws-us-east-1` or a
   GCP region near us-central1, and an API key for a service account.
2. Add a secret `staging-temporal-api-key` (add it to `local.secrets` in `secrets.tf`
   with `env = "TEMPORAL_API_KEY"` and readers `api` and `worker`, apply, then
   `printf %s "$KEY" | gcloud secrets versions add staging-temporal-api-key --data-file=-`).
3. Set `TEMPORAL_ADDRESS=<region>.<cloud>.api.temporal.io:7233` and
   `TEMPORAL_NAMESPACE=<namespace>` (in `run.tf`'s `common_env`) and apply. With an API key
   the worker does not register search attributes: add `StudyId`, `OrgId` (Keyword) and
   `Stage` (Keyword) in the Temporal Cloud namespace first.
4. Drain: let running studies finish on the VM, then delete `temporal.tf` (the data disk
   has `prevent_destroy`; remove that line to delete it on purpose).

### 1.5 Images: Artifact Registry

One image, `us-central1-docker.pkg.dev/cost-seg-basis/basis/basis-backend:<git sha>`,
built by CI from `backEnd/Dockerfile` (uv, locked dependencies, non-root user, the commit
in `GIT_SHA`). The API, worker, relay and jobs run it with different commands, so a
deploy is one build. The repository `basis` already existed (2026-10-15); Terraform only
grants the deployer `artifactregistry.writer` on it.

The image is 3.4 GB compressed, 6.2 GB on disk, of which 3.2 GB is the CUDA libraries
the default Linux `torch` wheel pulls in (via sentence-transformers); Cloud Run has no
GPU for them. Installing the CPU wheel on Linux (uv's documented
`[tool.uv.sources]` pytorch-cpu index) would roughly halve the image and its pull time
on cold starts. Proposed as a follow-up; not changed here because it changes the lockfile
every test runs on.

Storage: $0.10/GB-month above 0.5 GB free; consecutive images share their dependency
layers, so a few images cost well under $1/month. A cleanup policy (keep the last 10
`basis-backend` tags) is worth adding once deploys are frequent.

### 1.6 Secrets: Secret Manager

| Secret | Env var | Read by |
|---|---|---|
| `staging-database-url` | `DATABASE_URL` | API, worker, relay, jobs |
| `staging-openai-api-key` | `OPENAI_API_KEY` | worker |
| `staging-anthropic-api-key` | `ANTHROPIC_API_KEY` | worker |
| `staging-azure-di-key` | `AZURE_DOCUMENT_INTELLIGENCE_KEY` | worker |
| `staging-replicate-api-token` | `REPLICATE_API_TOKEN` | worker |

Terraform creates the secrets empty and grants `secretAccessor` per secret to the
accounts in the last column; `scripts/load_secrets.py` adds the values from the
operator's environment or `backEnd/.env` over stdin (never on a command line, never
printed, whitespace stripped, unchanged values skipped). Cloud Run injects the `latest`
version at instance start, so a rotated key reaches new instances; redeploy to roll
running ones. The API holds no provider key: it calls no model. Cost: $0.06 per active
version per month (about $0.30) plus $0.03 per 10,000 accesses.

Non-secret settings are plain environment variables in `run.tf` (`BASIS_ENV=staging`,
`GCS_BUCKET`, `FIREBASE_PROJECT_ID`, `TEMPORAL_ADDRESS`, `TEMPORAL_NAMESPACE`,
`LLM_PRIMARY=openai`, `AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT`, `COST_TABLE_PATH`,
`CORS_ALLOWED_ORIGINS`). `LLM_PRIMARY=openai` and no Azure OpenAI settings: with Azure
first the pool maps `gpt-6-luna`/`gpt-6.1-sol` requests onto the Azure deployments
(EVAL_REPORT 27.2 and 28 gotchas), so staging runs the measured models on OpenAI.
The model defaults themselves are the code's (`basis/config.py`), so staging runs what
the evaluations chose.

### 1.7 Buckets

* `staging-cost-seg-basis-uploads`: study documents. The browser PUTs to V4 signed URLs
  that sign the content type and the exact length (`basis/storage/gcs.py`); the bucket's
  CORS allows `PUT` with `Content-Type` and `x-goog-content-length-range` from the
  frontend origins. Signing on Cloud Run goes through the IAM `signBlob` API, so the API's
  account has `serviceAccountTokenCreator` on itself. Objects older than 90 days are
  deleted (staging only).
* `staging-cost-seg-basis-private`: deployment-only data that must never be in the
  repository: `corpus/*.pdf` (the six reference PDFs, input of the ingest job) and
  `costs/unit_costs_rsmeans_2020.json` (the licensed RSMeans-derived cost table).
  Versioned. Only the worker account can read it.
* Both: uniform bucket-level access, public access prevention enforced, Standard class
  ($0.020/GB-month; under $0.01/month at staging's size).

**The cost table** (decision for Bryan, proposed here): a private GCS object, mounted
read-only into the worker with Cloud Run's Cloud Storage volume (`/mnt/private`) and
loaded at startup through the existing `COST_TABLE_PATH` setting
(`basis/domain/cost_table.py`; a broken table stops the worker at startup). No code
change. Access is one IAM grant, audited by Cloud Audit Logs, and a new version is an
upload plus a worker restart, with the old one kept by bucket versioning. Secret Manager
would also work (the file is 24 KB, under its 64 KiB limit) but it is data, not a
credential, and a bucket keeps the corpus and the table in one place.

### 1.8 Jobs: migrations and corpus ingest

* `staging-basis-migrate`: `alembic upgrade head`. CI points it at the new image and
  runs it to completion before deploying any code.
* `staging-basis-ingest`: `python -m basis.retrieval.ingest`, 4 vCPU and 8 GiB, with the
  private bucket mounted; `scripts/ingest-corpus.sh` passes `--pdf /mnt/private/corpus/<id>.pdf
  --doc-id <id>` for each PDF. The worker builds the BM25 index of the latest published
  version at startup, so restart it after an ingest.

### 1.9 SAM 3: Replicate now, self-hosted Cloud Run GPU documented

The worker localizes objects with SAM 3 on Replicate (`REPLICATE_API_TOKEN`): $0.03 per
52-photo study, nothing idle, nothing to operate. `docs/review_2026-10-15/SAM3_HOSTING.md`
measured both options. Keep Replicate credit at $20 or more with auto-reload: under $10
Replicate caps creates at 60 a minute, under $5 at 6.

The self-hosted option is deployed but unused: Cloud Run service `staging-sam3` in
**us-east4** (one L4, 4 vCPU, 16 GiB, min 0, max 3, authenticated). us-central1 has no L4
quota on this project. It costs nothing idle; a lone study costs $0.13 to $0.20 because
an instance stays billed 6 to 10 minutes after its last request. To use it: set
`LOCALIZER_URL=<staging-sam3 url>` and `LOCALIZER_GOOGLE_AUTH=true` on the worker and
grant `staging-worker` `roles/run.invoker` on `staging-sam3`. Worth it at hundreds of
studies a day, if the Replicate tier cannot be kept up, or for data residency.

### 1.10 Network

A VPC of its own, `staging-basis` (the project's `default` network allows SSH and RDP
from anywhere and is left untouched), with one subnet `10.20.0.0/24`:

* Cloud Run (API, worker pool, jobs) uses Direct VPC egress for private ranges only:
  traffic to the Temporal VM goes through the VPC; model providers and Google APIs are
  reached directly. No Serverless VPC Access connector (which would bill its own VMs).
* The Temporal VM has a fixed internal address `10.20.0.10`, no external IP, Cloud NAT
  for image pulls. Firewall: TCP 7233 from the subnet only, TCP 22 from IAP's range
  (`35.235.240.0/20`) only.

### 1.11 Identities and least privilege

| Account | Project roles | Resource grants |
|---|---|---|
| `staging-api` | cloudsql.client, logging.logWriter, monitoring.metricWriter, cloudtrace.agent | secretAccessor on `staging-database-url`; storage.objectUser on uploads; serviceAccountTokenCreator on itself (signBlob) |
| `staging-worker` | the same four | secretAccessor on all five secrets; objectUser on uploads; objectViewer on private |
| `staging-temporal` | logging.logWriter, monitoring.metricWriter | none |
| `staging-deployer` | run.developer | artifactregistry.writer on repository `basis`; serviceAccountUser on `staging-api` and `staging-worker`; workloadIdentityUser for `repo:bryanrg22/Basis_Info:environment:staging` |

The deployer can push images and deploy or run the staging Cloud Run resources as the two
runtime accounts. It cannot change IAM (run.developer has no `setIamPolicy`), read
secrets, touch the database, or act as any other account. The older accounts created on
2026-10-15 (`github-deploy@`, `basis-api@`, `basis-worker@`) are unused and untouched.

### 1.12 CI deploys: Workload Identity Federation

No service-account key exists. GitHub's OIDC token is exchanged for a short-lived Google
token through the pool `github-actions`, provider `github`, which accepts only this
repository (by numeric id `1110700763`, which survives renames). Only a job in the GitHub
environment `staging` can impersonate the deployer, and that environment deploys only
from `main` (`scripts/github-environment.sh`). Pull requests, forks and other jobs get
tokens with other subjects and are refused.

The `deploy-staging` job (on a push to `main`, after backend lint and tests, the secret
scan, the client-data guard and the image build):

1. builds the image for linux/amd64 with the commit as `GIT_SHA` and pushes it;
2. runs the migrations job and waits for it (a failure stops the deploy before any new
   code runs; migrations must keep the running release working: expand first, contract
   in a later release);
3. deploys the worker pool (worker and relay), then the API;
4. checks that `/health` reports the new commit with the database and Temporal ok.

The job runs one at a time and is never cancelled (runs on `main` queue instead of
cancelling each other, since a migration must not be cut off). To require a manual
approval before each deploy: `REVIEWER=bryanrg22 infra/staging/scripts/github-environment.sh`.

**Rollback:** API: `gcloud run services update-traffic staging-basis-api --to-revisions
<previous revision>=100 --region us-central1`. Worker pool: redeploy the previous image,
`gcloud beta run worker-pools deploy staging-basis-worker --container worker --image <old>
--container relay --image <old> --region us-central1`. A migration is not rolled back
automatically; write its downgrade and run it deliberately.

## 2. Cost

### 2.1 Every month, at idle (always on)

| Item | Monthly |
|---|---|
| Cloud SQL db-g1-small ($25.55) + 10 GB SSD ($1.70) + backups (about $0.20) | $27.45 |
| Temporal VM e2-medium ($24.46) + 2 x 20 GB pd-balanced ($4.00) + snapshots (about $0.10) | $28.56 |
| Cloud NAT for the VM ($0.0014/h gateway + $0.005/h NAT IP) | $4.67 |
| Worker pool: 2 vCPU x $29.54 + 2.5 GiB x $3.26 (worker 1 vCPU/2 GiB, relay 1 vCPU/0.5 GiB) | $67.23 |
| API (scales to zero), jobs (run seconds per deploy) | $0 |
| Secret Manager, Artifact Registry, buckets, logging (within free tiers) | about $1.50 |
| **Total** | **about $129** |

At that rate the $2,000 GCP credit lasts about 15 months. The worker pool is half of it:
its relay sidecar's CPU is the cheapest saving if Cloud Run accepts a fractional CPU for a
sidecar (measured at deploy time, section 6).

**Parked** (`scripts/park.sh down`: worker pool at 0, VM stopped, Cloud SQL stopped):
disks $4.00 + Cloud SQL storage $1.70 + its reserved IP while stopped ($7.30) + backups
and the rest (about $2) = **about $15/month**. `park.sh up` brings it back in a few
minutes.

### 2.2 Per study

For the one real package (a 20-page appraisal and 52 photos) at the code's current
defaults:

| Item | Per study | Source |
|---|---|---|
| Model calls (OpenAI, Anthropic) | about $6.40 | EVAL_REPORT 27.8 live run $6.75, minus 28.4's $0.32 surfaces saving |
| Azure Document Intelligence (layout with key-value pairs, 20 pages) | about $0.20 to $0.30 | Azure list price, not recorded by the cost recorder: check the Azure bill |
| SAM 3 on Replicate (compact output) | $0.03 | SAM3_HOSTING.md section 4 |
| Cloud Run (API requests; the worker is already paid for), egress to providers | under $0.05 | Cloud Run free tier covers staging's requests |
| Temporal | $0 self-hosted (about $0.10 to $0.30 on Temporal Cloud, estimated) | section 1.4 |
| **Total** | **about $6.70** | |

Model calls are over 95% of a study's cost, and they scale with photos and assets: the
classifier made 359 calls on the live study (EVAL_REPORT 27.8). `STUDY_BUDGET_USD`
(default $25) stops a runaway study. Load tests should use small synthetic studies or
budget for about $6.70 per real-size study.

## 3. First deploy (runbook)

**Who can apply:** Terraform creates IAM bindings and a Workload Identity pool, so it
needs a project Owner, or these roles: `roles/resourcemanager.projectIamAdmin`,
`roles/iam.workloadIdentityPoolAdmin`, `roles/iam.serviceAccountAdmin`,
`roles/secretmanager.admin`, `roles/run.admin`, `roles/storage.admin` plus Editor for the
rest. On 2026-10-17 `bryanram2024@gmail.com` had Editor only (the Owner is
`adriancleve1@gmail.com`), which cannot create the pool or set any IAM policy.

From the repository root, with `gcloud` signed in as that account:

```bash
infra/staging/scripts/bootstrap.sh                 # state bucket + base APIs
cp infra/staging/terraform.tfvars.example infra/staging/terraform.tfvars   # fill in
cd infra/staging && terraform init && terraform apply && cd -
infra/staging/scripts/set-db-password.sh           # DB user + staging-database-url
uv run --project backEnd python infra/staging/scripts/load_secrets.py     # provider keys
infra/staging/scripts/upload-private.sh            # corpus PDFs + cost table
gcloud run jobs execute staging-basis-migrate --region us-central1 --wait
infra/staging/scripts/ingest-corpus.sh
gcloud beta run worker-pools update staging-basis-worker --region us-central1 --scaling 1
infra/staging/scripts/github-environment.sh        # staging env: main only, WIF vars
```

The first `terraform apply` needs an image that already exists (`image` in
`terraform.tfvars`): push one first, e.g. `docker buildx build --platform linux/amd64
--build-arg GIT_SHA=$(git rev-parse HEAD) -t <image> --push backEnd`. The Cloud Run
resources start before the secrets have versions and fail until they do; the scripts
after `apply` fix that, and the worker pool update restarts it.

## 4. Smoke test

`infra/staging/scripts/smoke.py` checks a deployed API end to end:

```bash
uv run --project backEnd python infra/staging/scripts/smoke.py "$API" --expect-sha "$SHA" \
  --token-file ~/.basis-staging-token
```

Without a token: `/health` (sha, database, Temporal), a 401 problem without a token, the
CORS preflight. With a Firebase ID token it also creates a study, uploads a synthetic
one-page appraisal and a drawn photo through signed URLs exactly as a browser does
(including the bucket's CORS preflight), confirms both, starts the workflow and polls
until it waits at the appraisal review gate. Nothing in it is client data.

**Auth for staging:** the API verifies real Firebase ID tokens of project
`cost-seg-basis` (the emulator is refused outside dev and test). Any Firebase user works:
the first request creates the user, a personal organization and an owner membership. A
token from the frontend's sign-in (`await auth.currentUser.getIdToken()` in the browser
console) is the simplest; it is valid for an hour.

## 5. Operations

### 5.1 Logs and metrics

Cloud Logging, JSON lines with the request id: `resource.type="cloud_run_revision"
resource.labels.service_name="staging-basis-api"`, `resource.type="cloud_run_worker_pool"`,
`resource.type="cloud_run_job"`, and the VM's containers under `gce_instance`
(`staging-temporal`). The worker serves Prometheus metrics on `:9464` inside its instance.

### 5.2 Temporal UI

```bash
gcloud compute ssh staging-temporal --zone us-central1-a --tunnel-through-iap -- -N -L 8080:127.0.0.1:8080
# then open http://localhost:8080
```

### 5.3 Routine changes

* New database credentials: `scripts/set-db-password.sh`, then redeploy (or
  `gcloud beta run worker-pools update ... --scaling 1` and a new API revision).
* Rotate a provider key: update it locally, `load_secrets.py --only <secret>`, redeploy.
* New cost table: `FORCE=1 scripts/upload-private.sh`, restart the worker pool.
* Re-ingest the corpus: `scripts/ingest-corpus.sh`, restart the worker pool.
* Change the VM's stack: edit `temporal/compose.yaml`, `terraform apply` (updates the
  metadata), then `gcloud compute instances reset staging-temporal --zone us-central1-a`
  (the startup script re-applies it on boot; data is on the data disk).
* Upgrade Temporal: bump the three image tags one minor version at a time; the schema job
  migrates on boot.

### 5.4 Park and unpark

`infra/staging/scripts/park.sh down|up|status` (section 2.1). The API stays deployed and
answers `/health` with 503 while the database is stopped.

## 6. Frontend

The API is ready for the frontend at its base URL (table at the top). To point the
frontend at staging, set in `frontend/.env.local` (or the hosting environment):

```bash
NEXT_PUBLIC_BACKEND_API_URL=https://staging-basis-api-930581733841.us-central1.run.app
NEXT_PUBLIC_BACKEND_URL=https://staging-basis-api-930581733841.us-central1.run.app
NEXT_PUBLIC_FIREBASE_PROJECT_ID=cost-seg-basis   # tokens must come from this project
```

(`src/config/env.ts` reads the first, `src/api/firestore/endpoints.api.ts` the second.)

CORS is allowed from `https://cost-seg-basis.web.app`, `https://cost-seg-basis.firebaseapp.com`
and `http://localhost:3000`, both on the API (`CORS_ALLOWED_ORIGINS`) and on the uploads
bucket (browser PUTs). To add an origin (a preview channel, App Hosting's URL), add it to
`cors_allowed_origins` in `infra/staging/variables.tf` (or `terraform.tfvars`) and
`terraform apply`: one list feeds both. Outside dev and test the API refuses `*` and
plain-http origins other than localhost at startup.

What the frontend must send: `Authorization: Bearer <Firebase ID token>` on `/v1`, an
`Idempotency-Key` (a UUID) on every POST and DELETE, and for uploads the two headers the
upload ticket returns (`Content-Type`, `x-goog-content-length-range`) on the PUT.
Responses expose `X-Request-Id`, `Idempotent-Replayed`, `Location` and `Retry-After`.

## 7. Before production

* Its own project (`cost-seg-basis-prod` or similar) and Terraform state, so staging
  credentials cannot reach production; the `production` GitHub environment already
  requires a reviewer.
* Temporal Cloud (section 1.4).
* Cloud SQL: a dedicated-core tier, regional (HA), point-in-time recovery, IAM database
  auth through the Cloud SQL Python Connector (`CLOUD_SQL_INSTANCE`).
* The CPU-only torch image (section 1.5).
* Firebase `check_revoked` is already on in `prod` (`basis/auth/firebase.py`).
* Uptime check and alerts on `/health`, worker restarts and Cloud SQL connections;
  Artifact Registry cleanup policy; a budget alert on the billing account.
