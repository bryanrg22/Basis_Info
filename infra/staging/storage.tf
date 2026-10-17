# Two private buckets (uniform access, public access prevention enforced).
#
# uploads: study files. Browsers PUT to V4 signed URLs the API issues (content type and
#          exact length signed), so the bucket's CORS lets the frontend's origins PUT
#          with those two headers. The worker reads photos and writes its outputs here.
# private: deployment-only data that must never be in the repository: the reference
#          corpus PDFs (input of the ingest job) and the licensed RSMeans-derived cost
#          table, which the worker mounts read-only and loads at startup (COST_TABLE_PATH).
#          Versioned, so a replaced table can be recovered.

resource "google_storage_bucket" "uploads" {
  name                        = "staging-${var.project_id}-uploads"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  cors {
    origin          = var.cors_allowed_origins
    method          = ["PUT"]
    response_header = ["Content-Type", "x-goog-content-length-range"]
    max_age_seconds = 3600
  }

  # Unfinished uploads and abandoned studies do not accumulate in staging.
  lifecycle_rule {
    condition {
      age = 90
    }
    action {
      type = "Delete"
    }
  }
}

resource "google_storage_bucket" "private" {
  name                        = "staging-${var.project_id}-private"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  versioning {
    enabled = true
  }
}

resource "google_storage_bucket_iam_member" "uploads" {
  for_each = local.runtime_accounts
  bucket   = google_storage_bucket.uploads.name
  role     = "roles/storage.objectUser"
  member   = "serviceAccount:${each.value}"
}

resource "google_storage_bucket_iam_member" "private_read" {
  bucket = google_storage_bucket.private.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.worker.email}"
}
