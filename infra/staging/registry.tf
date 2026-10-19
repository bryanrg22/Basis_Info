# Docker repository for the backend image (one image, tagged with the git sha, for the
# API, worker, relay and jobs). Deploys are frequent and each image is about 0.6 GB
# compressed, so untagged images are deleted after a week and only the 10 newest
# basis-backend versions are kept.

resource "google_artifact_registry_repository" "staging" {
  repository_id = "staging-basis"
  location      = var.region
  format        = "DOCKER"
  description   = "Basis backend images for staging"

  cleanup_policy_dry_run = false

  cleanup_policies {
    id     = "keep-10-newest"
    action = "KEEP"
    most_recent_versions {
      package_name_prefixes = ["basis-backend"]
      keep_count            = 10
    }
  }

  cleanup_policies {
    id     = "delete-untagged-after-7-days"
    action = "DELETE"
    condition {
      tag_state  = "UNTAGGED"
      older_than = "604800s"
    }
  }

  depends_on = [google_project_service.enabled]
}
