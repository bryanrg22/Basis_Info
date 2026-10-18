#!/usr/bin/env bash
# One-time setup before the first `terraform init` (docs/deploy/STAGING.md, "First deploy").
#
# Creates the Terraform state bucket (versioned, private) and enables the APIs Terraform
# itself needs before it can manage the rest. Safe to re-run.
#
# Needs: gcloud signed in as a project Owner (or an account with the roles listed in
# STAGING.md, "Who can apply").
set -euo pipefail

PROJECT=${PROJECT:-basis-staging-512000}
REGION=${REGION:-us-central1}
STATE_BUCKET=${STATE_BUCKET:-basis-staging-512000-tfstate}

gcloud services enable --project "$PROJECT" \
  cloudresourcemanager.googleapis.com serviceusage.googleapis.com iam.googleapis.com \
  storage.googleapis.com

if ! gcloud storage buckets describe "gs://$STATE_BUCKET" --project "$PROJECT" >/dev/null 2>&1; then
  gcloud storage buckets create "gs://$STATE_BUCKET" --project "$PROJECT" \
    --location "$REGION" --uniform-bucket-level-access --public-access-prevention
fi
gcloud storage buckets update "gs://$STATE_BUCKET" --versioning
echo "state bucket gs://$STATE_BUCKET ready"
