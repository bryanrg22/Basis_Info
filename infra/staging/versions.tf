# Staging environment of the Basis backend in its own GCP project, basis-staging-512000.
# Architecture, costs and runbook: docs/deploy/STAGING.md.
#
# State lives in a versioned private bucket (scripts/bootstrap.sh creates it). Secret
# values are never in Terraform: it creates the Secret Manager secrets empty and
# scripts/load-secrets.sh adds their versions, so the state holds no key or password.

terraform {
  required_version = ">= 1.9"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 8.5"
    }
  }

  backend "gcs" {
    bucket = "basis-staging-512000-tfstate"
    prefix = "staging"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region

  default_labels = {
    env        = "staging"
    app        = "basis"
    managed-by = "terraform"
  }
}
