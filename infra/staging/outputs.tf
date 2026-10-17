output "api_url" {
  description = "Base URL of the staging API (the frontend's NEXT_PUBLIC_BACKEND_API_URL)."
  value       = google_cloud_run_v2_service.api.uri
}

output "workload_identity_provider" {
  description = "For google-github-actions/auth in the deploy job."
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "deployer_service_account" {
  value = google_service_account.deployer.email
}

output "uploads_bucket" {
  value = google_storage_bucket.uploads.name
}

output "private_bucket" {
  value = google_storage_bucket.private.name
}

output "sql_connection_name" {
  value = google_sql_database_instance.staging.connection_name
}

output "temporal_address" {
  value = "${google_compute_address.temporal.address}:7233"
}
