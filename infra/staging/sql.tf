# Cloud SQL for PostgreSQL 16 (pgvector is a supported extension; migration 0001 creates it).
#
# Enterprise edition, shared-core db-g1-small, one zone: the smallest tier whose
# max_connections (50) covers the API (4 x 5 pooled), the worker (5), the relay (5) and a
# job (5) at once; db-f1-micro allows 25. No SLA on shared-core tiers, acceptable for
# staging. Costs in STAGING.md.
#
# Connections: public IP with no authorized networks, so only the Cloud SQL connector (the
# Cloud Run built-in socket at /cloudsql/<connection name>, authorized by IAM
# roles/cloudsql.client) reaches it, encrypted. The application user and its password are
# created by scripts/set-db-password.sh, which writes the database URL straight into
# Secret Manager; Terraform never sees the password.

resource "google_sql_database_instance" "staging" {
  name                = "staging-basis-pg"
  region              = var.region
  database_version    = "POSTGRES_16"
  deletion_protection = true

  settings {
    edition                     = "ENTERPRISE"
    tier                        = var.sql_tier
    activation_policy           = var.parked ? "NEVER" : "ALWAYS" # NEVER: stopped, storage kept
    availability_type           = "ZONAL"
    disk_type                   = "PD_SSD"
    disk_size                   = 10
    disk_autoresize             = true
    deletion_protection_enabled = true

    backup_configuration {
      enabled                        = true
      start_time                     = "08:00" # UTC, 01:00 Pacific
      point_in_time_recovery_enabled = false
      backup_retention_settings {
        retained_backups = 7
      }
    }

    ip_configuration {
      ipv4_enabled = true
      ssl_mode     = "ENCRYPTED_ONLY"
    }

    maintenance_window {
      day          = 7 # Sunday
      hour         = 9 # UTC
      update_track = "stable"
    }

    insights_config {
      query_insights_enabled = true
    }

    user_labels = {
      env = "staging"
      app = "basis"
    }
  }

  depends_on = [google_project_service.enabled]
}

resource "google_sql_database" "basis" {
  name     = "basis"
  instance = google_sql_database_instance.staging.name
}
