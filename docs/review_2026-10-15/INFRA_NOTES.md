# Infrastructure notes (cost-seg-basis)

Record of what exists, what was created during the rebuild, and what still needs an Owner account. No secrets here.

## Created 2026-10-15 (by the orchestrator, as bryanram2024@gmail.com with roles/editor)

| Resource | Name | Notes |
|---|---|---|
| APIs enabled | sqladmin, sts, cloudresourcemanager, compute, run, artifactregistry, secretmanager, iamcredentials, cloudtrace, monitoring, logging | |
| Artifact Registry (Docker) | `us-central1-docker.pkg.dev/cost-seg-basis/basis` | images tagged by git SHA |
| GCS bucket | `gs://basis-eval-private` | uniform access, public access prevention; holds client evaluation data; never referenced from CI |
| Service account | `github-deploy@cost-seg-basis.iam.gserviceaccount.com` | used by GitHub Actions via WIF (bindings pending) |
| Service account | `basis-api@cost-seg-basis.iam.gserviceaccount.com` | Cloud Run API runtime identity |
| Service account | `basis-worker@cost-seg-basis.iam.gserviceaccount.com` | Cloud Run worker pool runtime identity |
| GitHub environment | `staging` | no protection rules |
| GitHub environment | `production` | required reviewer `bryanrg22`; deployments only from `main` |
| GitHub security | Dependabot security updates, secret scanning, push protection | enabled |

Pre-existing and untouched: Firebase App Hosting backend `basisbackend`, Hosting site, Firestore (rules of 2025-12-01), buckets `basis-evidence-layer-prod` and `cost-seg-basis.firebasestorage.app`, service accounts `basis-backend@`, `firebase-adminsdk-fbsvc@`, `firebase-app-hosting-compute@`.

## Blocked: needs a project Owner (bryanram2024@gmail.com has Editor only)

Editor cannot create Workload Identity pools or change IAM policy. Either grant `roles/owner` (or `roles/iam.workloadIdentityPoolAdmin` + `roles/resourcemanager.projectIamAdmin` + `roles/iam.serviceAccountAdmin`) to `bryanram2024@gmail.com`, or run the following as the Owner. Project number is `930581733841`.

```bash
P=cost-seg-basis; PN=930581733841
gcloud iam workload-identity-pools create github --location=global --display-name="GitHub Actions" --project $P
gcloud iam workload-identity-pools providers create-oidc github \
  --workload-identity-pool=github --location=global --project $P \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.actor=assertion.actor,attribute.repository=assertion.repository,attribute.repository_owner=assertion.repository_owner" \
  --attribute-condition="assertion.repository_owner == 'bryanrg22'"
gcloud iam service-accounts add-iam-policy-binding github-deploy@$P.iam.gserviceaccount.com --project $P \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/$PN/locations/global/workloadIdentityPools/github/attribute.repository/bryanrg22/Basis_Info"
for role in roles/run.admin roles/artifactregistry.writer roles/cloudsql.client; do
  gcloud projects add-iam-policy-binding $P --member="serviceAccount:github-deploy@$P.iam.gserviceaccount.com" --role=$role --condition=None
done
for sa in basis-api basis-worker; do
  gcloud iam service-accounts add-iam-policy-binding $sa@$P.iam.gserviceaccount.com --project $P \
    --member="serviceAccount:github-deploy@$P.iam.gserviceaccount.com" --role=roles/iam.serviceAccountUser
done
# runtime identities (Stage 5): secret access, Cloud SQL client, storage on the study bucket
for sa in basis-api basis-worker; do
  for role in roles/secretmanager.secretAccessor roles/cloudsql.client roles/cloudtrace.agent roles/logging.logWriter roles/monitoring.metricWriter; do
    gcloud projects add-iam-policy-binding $P --member="serviceAccount:$sa@$P.iam.gserviceaccount.com" --role=$role --condition=None
  done
done
```

After this runs, the GitHub workflow value is:
`workload_identity_provider: projects/930581733841/locations/global/workloadIdentityPools/github/providers/github`
`service_account: github-deploy@cost-seg-basis.iam.gserviceaccount.com`

## Deferred until the stage that needs it (ongoing cost)

- Cloud SQL for PostgreSQL instance `basis-staging` (Enterprise, smallest tier, us-central1, pgvector, backups + PITR). Created in Stage 1 when integration tests move from local Docker to a shared staging DB, or Stage 5 at the latest.
- Temporal Cloud namespaces `basis-staging` / `basis-prod` in `aws-us-east-1` (Bryan creates the account; fallback is self-hosting on one Compute Engine VM).
- Cloud Run service `basis-api-staging` and worker pool `basis-worker-staging` (Stage 5).
