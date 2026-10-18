# Service accounts, one per workload, each with only what it calls.
#
#   staging-api       Cloud Run service: Cloud SQL, the database URL secret, the uploads
#                     bucket (sign PUT URLs, stat, read, delete), signBlob on itself.
#   staging-worker    Cloud Run worker pool (worker + relay) and the migrate/ingest jobs:
#                     Cloud SQL, the provider key secrets, the uploads bucket, read-only
#                     on the private bucket (reference corpus, cost table).
#   staging-temporal  The Temporal VM: write its logs and metrics, nothing else.
#   staging-deployer  GitHub Actions through Workload Identity Federation (wif.tf): push
#                     images, deploy the Cloud Run resources and run their jobs, act as the
#                     two runtime accounts. No IAM, secret or database rights.
#
# Runtime accounts get project roles only where the resource has no IAM of its own
# (Cloud SQL client, logging, monitoring, tracing); everything else is granted on the
# resource (secrets.tf, storage.tf, below).

resource "google_service_account" "api" {
  account_id   = "staging-api"
  display_name = "Basis staging API (Cloud Run)"
}

resource "google_service_account" "worker" {
  account_id   = "staging-worker"
  display_name = "Basis staging worker, relay and jobs (Cloud Run)"
}

resource "google_service_account" "temporal" {
  account_id   = "staging-temporal"
  display_name = "Basis staging Temporal VM"
}

resource "google_service_account" "deployer" {
  account_id   = "staging-deployer"
  display_name = "Basis staging deploys from GitHub Actions (WIF)"
}

locals {
  runtime_project_roles = [
    "roles/cloudsql.client",
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/cloudtrace.agent",
  ]
  runtime_accounts = {
    api    = google_service_account.api.email
    worker = google_service_account.worker.email
  }
  runtime_bindings = {
    for pair in setproduct(keys(local.runtime_accounts), local.runtime_project_roles) :
    "${pair[0]}:${pair[1]}" => { account = local.runtime_accounts[pair[0]], role = pair[1] }
  }
}

resource "google_project_iam_member" "runtime" {
  for_each = local.runtime_bindings
  project  = var.project_id
  role     = each.value.role
  member   = "serviceAccount:${each.value.account}"
}

resource "google_project_iam_member" "temporal" {
  for_each = toset(["roles/logging.logWriter", "roles/monitoring.metricWriter"])
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.temporal.email}"
}

# Signed upload URLs: Cloud Run's metadata credentials cannot sign, so GcsStorage signs
# through the IAM signBlob API as the API's own account (basis/storage/gcs.py).
resource "google_service_account_iam_member" "api_signs_as_itself" {
  service_account_id = google_service_account.api.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.api.email}"
}

# The deployer deploys and runs Cloud Run resources (roles/run.developer has no IAM
# permissions, so it cannot open a service to the public or grant itself anything) ...
resource "google_project_iam_member" "deployer_run" {
  project = var.project_id
  role    = "roles/run.developer"
  member  = "serviceAccount:${google_service_account.deployer.email}"
}

# ... as the runtime accounts, and only those ...
resource "google_service_account_iam_member" "deployer_acts_as" {
  for_each           = { api = google_service_account.api.name, worker = google_service_account.worker.name }
  service_account_id = each.value
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.deployer.email}"
}

# ... and pushes images to the staging repository (registry.tf).
resource "google_artifact_registry_repository_iam_member" "deployer_push" {
  location   = google_artifact_registry_repository.staging.location
  repository = google_artifact_registry_repository.staging.name
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.deployer.email}"
}

# Cloud Run pulls images as its service agent, which reads repositories of its own project
# through roles/run.serviceAgent.
