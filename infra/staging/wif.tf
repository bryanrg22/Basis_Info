# Workload Identity Federation for GitHub Actions: no service-account key exists anywhere.
#
# The provider accepts tokens from this one repository (by numeric id, which survives a
# rename and cannot be re-registered by someone else). Only a job running in the GitHub
# environment `staging` may impersonate the deployer: the binding's subject is
# repo:<owner>/<name>:environment:staging, and the environment only deploys from main
# (scripts/github-environment.sh). A pull request, a fork or another workflow job gets a
# token with a different subject and is refused.

resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "github-actions"
  display_name              = "GitHub Actions"
  description               = "OIDC tokens from GitHub Actions (staging deploys)"
  depends_on                = [google_project_service.enabled]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  display_name                       = "GitHub"

  attribute_mapping = {
    "google.subject"          = "assertion.sub"
    "attribute.repository"    = "assertion.repository"
    "attribute.repository_id" = "assertion.repository_id"
    "attribute.environment"   = "assertion.environment"
    "attribute.ref"           = "assertion.ref"
    "attribute.workflow_ref"  = "assertion.workflow_ref"
  }
  attribute_condition = "assertion.repository_id == '${var.github_repository_id}'"

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account_iam_member" "deployer_from_github_staging" {
  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member = join("/", [
    "principal://iam.googleapis.com/projects/${var.project_number}/locations/global",
    "workloadIdentityPools/${google_iam_workload_identity_pool.github.workload_identity_pool_id}",
    "subject/repo:${var.github_repository}:environment:staging",
  ])
}
