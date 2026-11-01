# Two private buckets (uniform access, public access prevention enforced).
#
# uploads: study files. Browsers PUT to V4 signed URLs the API issues (content type and
#          exact length signed), so the bucket's CORS lets the frontend's origins PUT
#          with those two headers. The worker reads photos and writes its outputs here.
#          Versioned (RUNBOOK.md section 2): a delete or an overwrite leaves the previous
#          version in the bucket as a noncurrent version for uploads_noncurrent_days (30),
#          and GCS's soft delete keeps a deleted version 7 more days after that, so a file
#          deleted by mistake (or by the retention purge, basis.retention) is recoverable
#          for 37 days. A lifecycle rule also caps the versions one object keeps at 10,
#          for objects the worker rewrites on a retry (exports).
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

  versioning {
    enabled = true
  }

  # Soft delete: GCS's default for new buckets, stated so a plan shows any drift from it.
  # Deleted versions (lifecycle included) can be restored for 7 days (RUNBOOK.md 2.3).
  soft_delete_policy {
    retention_duration_seconds = 604800
  }

  # Staging only: unfinished uploads and abandoned studies do not accumulate. With
  # versioning on, this makes the live object noncurrent (recoverable below) rather than
  # gone. Production relies on the firm's retention rule (basis.retention) instead.
  lifecycle_rule {
    condition {
      age        = 90
      with_state = "LIVE"
    }
    action {
      type = "Delete"
    }
  }

  # Noncurrent versions (a deleted or overwritten object) are kept this long, then deleted
  # for good (after the soft-delete window).
  lifecycle_rule {
    condition {
      days_since_noncurrent_time = var.uploads_noncurrent_days
      with_state                 = "ARCHIVED"
    }
    action {
      type = "Delete"
    }
  }

  # An object rewritten many times (an export retried) keeps at most 10 older versions.
  lifecycle_rule {
    condition {
      num_newer_versions = 10
      with_state         = "ARCHIVED"
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
