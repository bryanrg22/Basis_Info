# Self-hosted Temporal on one VM (STAGING.md, Temporal: why not Temporal Cloud yet, and
# how to switch). The stack is temporal/compose.yaml, started by temporal/startup.sh.
#
# * e2-medium (1 vCPU sustained, 4 GB): Temporal server, its PostgreSQL and the UI.
# * State on a separate 20 GB data disk with daily snapshots kept 7 days, so the VM itself
#   is disposable (a new image or machine type recreates it; the disk is kept).
# * No external IP: Cloud NAT for image pulls, IAP for SSH; gRPC 7233 reachable from the
#   Cloud Run subnet only (network.tf). No TLS or auth on the frontend inside the VPC; the
#   STAGING.md production checklist covers mTLS or Temporal Cloud.

resource "google_compute_address" "temporal" {
  name         = "staging-temporal-internal"
  region       = var.region
  subnetwork   = google_compute_subnetwork.staging.id
  address_type = "INTERNAL"
  address      = "10.20.0.10"
  depends_on   = [google_project_service.enabled]
}

resource "google_compute_resource_policy" "temporal_snapshots" {
  name       = "staging-temporal-daily"
  region     = var.region
  depends_on = [google_project_service.enabled]

  snapshot_schedule_policy {
    schedule {
      daily_schedule {
        days_in_cycle = 1
        start_time    = "09:00" # UTC
      }
    }
    retention_policy {
      max_retention_days    = 7
      on_source_disk_delete = "KEEP_AUTO_SNAPSHOTS"
    }
  }
}

resource "google_compute_disk" "temporal_data" {
  name = "staging-temporal-data"
  zone = var.zone
  type = "pd-balanced"
  size = 20

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [google_project_service.enabled]
}

resource "google_compute_disk_resource_policy_attachment" "temporal_data" {
  name = google_compute_resource_policy.temporal_snapshots.name
  disk = google_compute_disk.temporal_data.name
  zone = var.zone
}

resource "google_compute_instance" "temporal" {
  name                      = "staging-temporal"
  zone                      = var.zone
  machine_type              = var.temporal_machine_type
  tags                      = ["staging-temporal"]
  allow_stopping_for_update = true
  desired_status            = var.parked ? "TERMINATED" : "RUNNING" # TERMINATED: stopped, disks kept

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
      size  = 20
      type  = "pd-balanced"
    }
  }

  attached_disk {
    source      = google_compute_disk.temporal_data.id
    device_name = "temporal-data"
  }

  network_interface {
    subnetwork = google_compute_subnetwork.staging.id
    network_ip = google_compute_address.temporal.address
    # No access_config: no external IP.
  }

  service_account {
    email  = google_service_account.temporal.email
    scopes = ["cloud-platform"]
  }

  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }

  metadata = {
    enable-oslogin         = "TRUE"
    startup-script         = file("${path.module}/temporal/startup.sh")
    temporal-compose       = file("${path.module}/temporal/compose.yaml")
    temporal-dynamicconfig = file("${path.module}/temporal/dynamicconfig.yaml")
    temporal-namespace     = var.temporal_namespace
  }

  depends_on = [google_compute_router_nat.staging, google_project_iam_member.temporal]
}
