# Cloud Run: the API (service), the Temporal worker and the outbox relay (one worker pool,
# relay as a sidecar), and two jobs (Alembic migrations, reference-corpus ingest). All run
# the one backend image with different commands.
#
# Images: Terraform sets the first one (var.image); CI deploys every later one with
# gcloud and Terraform ignores image changes (lifecycle below), so `terraform apply`
# never rolls a deployment back. Everything else here is Terraform's.
#
# Why a worker pool and not a service: a Temporal worker long-polls the task queue and has
# no HTTP traffic. A service with min-instances 1 bills that instance at the idle rate but
# throttles its CPU between requests, so polling would stall; with CPU always allocated a
# service costs the instance-based rate (us-central1 $0.000018/vCPU-s) against the worker
# pool's $0.00001124/vCPU-s, and a worker pool needs no ingress at all. Worker pools do
# not autoscale: the instance count is set (var.worker_instances, 0 parks staging).

locals {
  sql_connection = google_sql_database_instance.staging.connection_name

  # Settings every process reads (basis/config.py). Secrets come from secrets.tf.
  common_env = merge(var.app_env, {
    BASIS_ENV           = "staging"
    GCS_BUCKET          = google_storage_bucket.uploads.name
    FIREBASE_PROJECT_ID = var.project_id
    TEMPORAL_ADDRESS    = "${google_compute_address.temporal.address}:7233"
    TEMPORAL_NAMESPACE  = var.temporal_namespace
  })

  worker_env = merge(local.common_env, {
    AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT = var.azure_document_intelligence_endpoint
    COST_TABLE_PATH                      = "/mnt/private/${var.cost_table_object}"
  })

  # Where the worker and the ingest job see the private bucket (mounted read-only).
  private_mount = "/mnt/private"
}

# --------------------------------------------------------------------------- API

resource "google_cloud_run_v2_service" "api" {
  name                = "staging-basis-api"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false

  template {
    service_account                  = google_service_account.api.email
    max_instance_request_concurrency = 40
    timeout                          = "60s"

    scaling {
      min_instance_count = 0
      max_instance_count = var.api_max_instances
    }

    vpc_access {
      egress = "PRIVATE_RANGES_ONLY"
      network_interfaces {
        network    = google_compute_network.staging.id
        subnetwork = google_compute_subnetwork.staging.id
      }
    }

    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [local.sql_connection]
      }
    }

    containers {
      name  = "api"
      image = var.image

      ports {
        container_port = 8000
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
        cpu_idle          = true
        startup_cpu_boost = true
      }

      dynamic "env" {
        for_each = merge(local.common_env, {
          CORS_ALLOWED_ORIGINS = join(",", var.cors_allowed_origins)
        })
        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = local.api_secret_env
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.secret[env.value].secret_id
              version = "latest"
            }
          }
        }
      }

      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }

      startup_probe {
        tcp_socket {
          port = 8000
        }
        initial_delay_seconds = 0
        period_seconds        = 2
        failure_threshold     = 60
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].containers[0].image, client, client_version]
  }

  depends_on = [
    google_project_iam_member.runtime,
    google_secret_manager_secret_iam_member.reader,
  ]
}

# The API is public: every /v1 route verifies a Firebase ID token itself, and /health and
# the OpenAPI document are meant to be unauthenticated.
resource "google_cloud_run_v2_service_iam_member" "api_public" {
  name     = google_cloud_run_v2_service.api.name
  location = google_cloud_run_v2_service.api.location
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# --------------------------------------------------------------------------- worker + relay

resource "google_cloud_run_v2_worker_pool" "worker" {
  name                = "staging-basis-worker"
  location            = var.region
  deletion_protection = false
  launch_stage        = "GA"

  scaling {
    scaling_mode          = "MANUAL"
    manual_instance_count = var.worker_instances
  }

  template {
    service_account = google_service_account.worker.email

    vpc_access {
      egress = "PRIVATE_RANGES_ONLY"
      network_interfaces {
        network    = google_compute_network.staging.id
        subnetwork = google_compute_subnetwork.staging.id
      }
    }

    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [local.sql_connection]
      }
    }

    volumes {
      name = "private"
      gcs {
        bucket    = google_storage_bucket.private.name
        read_only = true
      }
    }

    containers {
      name    = "worker"
      image   = var.image
      command = ["python", "-m", "basis.worker"]

      resources {
        limits = {
          cpu    = "1"
          memory = "2Gi"
        }
      }

      dynamic "env" {
        for_each = local.worker_env
        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = local.worker_secret_env
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.secret[env.value].secret_id
              version = "latest"
            }
          }
        }
      }

      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }

      volume_mounts {
        name       = "private"
        mount_path = local.private_mount
      }

      # basis.worker.health: 200 once connected to Temporal and polling.
      startup_probe {
        http_get {
          path = "/health"
          port = 8080
        }
        period_seconds    = 5
        failure_threshold = 60
      }
    }

    containers {
      name    = "relay"
      image   = var.image
      command = ["python", "-m", "basis.relay"]

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }

      dynamic "env" {
        for_each = merge(local.common_env, { DB_POOL_SIZE = "2" })
        content {
          name  = env.key
          value = env.value
        }
      }

      dynamic "env" {
        for_each = local.api_secret_env # the database URL only
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = google_secret_manager_secret.secret[env.value].secret_id
              version = "latest"
            }
          }
        }
      }

      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }

      # basis.relay: 200 while the relay reaches the database.
      startup_probe {
        http_get {
          path = "/health"
          port = 8081
        }
        period_seconds    = 5
        failure_threshold = 30
      }
    }
  }

  lifecycle {
    ignore_changes = [
      template[0].containers[0].image,
      template[0].containers[1].image,
      client,
      client_version,
    ]
  }

  depends_on = [
    google_project_iam_member.runtime,
    google_secret_manager_secret_iam_member.reader,
    google_storage_bucket_iam_member.private_read,
    google_compute_instance.temporal,
  ]
}

# --------------------------------------------------------------------------- jobs

# `alembic upgrade head`; CI runs it before each deploy and waits for it.
resource "google_cloud_run_v2_job" "migrate" {
  name                = "staging-basis-migrate"
  location            = var.region
  deletion_protection = false

  template {
    task_count = 1

    template {
      service_account = google_service_account.worker.email
      max_retries     = 0
      timeout         = "600s"

      volumes {
        name = "cloudsql"
        cloud_sql_instance {
          instances = [local.sql_connection]
        }
      }

      containers {
        image   = var.image
        command = ["alembic", "upgrade", "head"]

        resources {
          limits = {
            cpu    = "1"
            memory = "1Gi"
          }
        }

        dynamic "env" {
          for_each = local.common_env
          content {
            name  = env.key
            value = env.value
          }
        }

        dynamic "env" {
          for_each = local.api_secret_env
          content {
            name = env.key
            value_source {
              secret_key_ref {
                secret  = google_secret_manager_secret.secret[env.value].secret_id
                version = "latest"
              }
            }
          }
        }

        volume_mounts {
          name       = "cloudsql"
          mount_path = "/cloudsql"
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].template[0].containers[0].image, client, client_version]
  }

  depends_on = [google_project_iam_member.runtime, google_secret_manager_secret_iam_member.reader]
}

# `python -m basis.retrieval.ingest` over the corpus PDFs in the private bucket; run by
# scripts/ingest-corpus.sh with the --pdf/--doc-id arguments as an execution override.
resource "google_cloud_run_v2_job" "ingest" {
  name                = "staging-basis-ingest"
  location            = var.region
  deletion_protection = false

  template {
    task_count = 1

    template {
      service_account = google_service_account.worker.email
      max_retries     = 0
      timeout         = "3600s"

      volumes {
        name = "cloudsql"
        cloud_sql_instance {
          instances = [local.sql_connection]
        }
      }

      volumes {
        name = "private"
        gcs {
          bucket    = google_storage_bucket.private.name
          read_only = true
        }
      }

      containers {
        image   = var.image
        command = ["python", "-m", "basis.retrieval.ingest"]
        args    = ["--list-versions"]

        resources {
          limits = {
            cpu    = "4"
            memory = "8Gi"
          }
        }

        dynamic "env" {
          for_each = local.common_env
          content {
            name  = env.key
            value = env.value
          }
        }

        dynamic "env" {
          for_each = local.api_secret_env
          content {
            name = env.key
            value_source {
              secret_key_ref {
                secret  = google_secret_manager_secret.secret[env.value].secret_id
                version = "latest"
              }
            }
          }
        }

        volume_mounts {
          name       = "cloudsql"
          mount_path = "/cloudsql"
        }

        volume_mounts {
          name       = "private"
          mount_path = local.private_mount
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [template[0].template[0].containers[0].image, client, client_version]
  }

  depends_on = [
    google_project_iam_member.runtime,
    google_secret_manager_secret_iam_member.reader,
    google_storage_bucket_iam_member.private_read,
  ]
}
