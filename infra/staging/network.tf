# A VPC of its own (the project's default network allows SSH and RDP from anywhere).
#
# * Cloud Run (API, worker pool, jobs) joins the subnet with Direct VPC egress, private
#   ranges only: traffic to the Temporal VM goes through the VPC, everything else
#   (model providers, Google APIs) leaves Cloud Run directly. No connector to pay for.
# * The Temporal VM has no external IP; Cloud NAT lets it pull images, IAP lets an
#   operator SSH in (and tunnel to the Temporal UI).
# * Ingress to the VM: gRPC 7233 from the subnet only, SSH from IAP's range only.

resource "google_compute_network" "staging" {
  name                    = "staging-basis"
  auto_create_subnetworks = false
  depends_on              = [google_project_service.enabled]
}

resource "google_compute_subnetwork" "staging" {
  name                     = "staging-basis-${var.region}"
  region                   = var.region
  network                  = google_compute_network.staging.id
  ip_cidr_range            = "10.20.0.0/24"
  private_ip_google_access = true
}

resource "google_compute_router" "staging" {
  name    = "staging-basis-router"
  region  = var.region
  network = google_compute_network.staging.id
}

resource "google_compute_router_nat" "staging" {
  name                               = "staging-basis-nat"
  router                             = google_compute_router.staging.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"

  log_config {
    enable = true
    filter = "ERRORS_ONLY"
  }
}

resource "google_compute_firewall" "temporal_grpc" {
  name          = "staging-allow-temporal-grpc"
  network       = google_compute_network.staging.id
  direction     = "INGRESS"
  source_ranges = [google_compute_subnetwork.staging.ip_cidr_range]
  target_tags   = ["staging-temporal"]

  allow {
    protocol = "tcp"
    ports    = ["7233"]
  }
}

resource "google_compute_firewall" "iap_ssh" {
  name          = "staging-allow-iap-ssh"
  network       = google_compute_network.staging.id
  direction     = "INGRESS"
  source_ranges = ["35.235.240.0/20"] # Identity-Aware Proxy TCP forwarding
  target_tags   = ["staging-temporal"]

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}
