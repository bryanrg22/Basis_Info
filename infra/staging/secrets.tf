# Secret Manager secrets, created empty: scripts/load-secrets.sh adds the versions from the
# operator's shell (never from Terraform, so no value is in the state). Cloud Run injects
# the latest version as an environment variable at instance start.
#
# Who reads what:
#   database-url        API, worker, relay, jobs (it carries the database password)
#   provider keys       worker only (the API calls no model), and the Azure DI endpoint

locals {
  secrets = {
    "staging-database-url"      = { env = "DATABASE_URL", readers = ["api", "worker"] }
    "staging-openai-api-key"    = { env = "OPENAI_API_KEY", readers = ["worker"] }
    "staging-anthropic-api-key" = { env = "ANTHROPIC_API_KEY", readers = ["worker"] }
    "staging-azure-di-key"      = { env = "AZURE_DOCUMENT_INTELLIGENCE_KEY", readers = ["worker"] }
    # Not a credential, but it names the Azure resource; kept out of the public repo.
    "staging-azure-di-endpoint"   = { env = "AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT", readers = ["worker"] }
    "staging-replicate-api-token" = { env = "REPLICATE_API_TOKEN", readers = ["worker"] }
  }
  secret_readers = merge([
    for name, secret in local.secrets : {
      for reader in secret.readers : "${name}:${reader}" => { secret = name, reader = reader }
    }
  ]...)
  # Secrets as Cloud Run env entries, per workload.
  worker_secret_env = { for name, secret in local.secrets : secret.env => name }
  api_secret_env = {
    for name, secret in local.secrets : secret.env => name if contains(secret.readers, "api")
  }
}

resource "google_secret_manager_secret" "secret" {
  for_each  = local.secrets
  secret_id = each.key

  replication {
    auto {}
  }

  depends_on = [google_project_service.enabled]
}

resource "google_secret_manager_secret_iam_member" "reader" {
  for_each  = local.secret_readers
  secret_id = google_secret_manager_secret.secret[each.value.secret].id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${local.runtime_accounts[each.value.reader]}"
}
