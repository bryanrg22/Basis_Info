#!/usr/bin/env bash
# Park staging when nobody is using it, or bring it back (STAGING.md, "Cost").
#
#   park.sh down   worker pool to 0 instances, Temporal VM stopped, Cloud SQL stopped.
#                  Left billing: disks, backups, the stopped instance's IP (about $12/month).
#   park.sh up     the reverse, in dependency order; the worker reconnects on start.
#   park.sh status what is running.
#
# The API scales to zero by itself and needs nothing. While parked it answers /health with
# 503 (no database), and studies cannot start. Terraform's worker_instances stays the
# source of truth for "up": a later `terraform apply` brings the worker back to it.
set -euo pipefail

PROJECT=${PROJECT:-cost-seg-basis}
REGION=${REGION:-us-central1}
ZONE=${ZONE:-us-central1-a}
SQL=staging-basis-pg
VM=staging-temporal
POOL=staging-basis-worker

case "${1:-status}" in
  down)
    gcloud beta run worker-pools update "$POOL" --project "$PROJECT" --region "$REGION" --scaling 0
    gcloud compute instances stop "$VM" --project "$PROJECT" --zone "$ZONE"
    gcloud sql instances patch "$SQL" --project "$PROJECT" --activation-policy NEVER --quiet
    ;;
  up)
    gcloud sql instances patch "$SQL" --project "$PROJECT" --activation-policy ALWAYS --quiet
    gcloud compute instances start "$VM" --project "$PROJECT" --zone "$ZONE"
    gcloud beta run worker-pools update "$POOL" --project "$PROJECT" --region "$REGION" --scaling 1
    ;;
  status)
    gcloud sql instances describe "$SQL" --project "$PROJECT" \
      --format='value(name,state,settings.activationPolicy)'
    gcloud compute instances describe "$VM" --project "$PROJECT" --zone "$ZONE" \
      --format='value(name,status)'
    gcloud beta run worker-pools describe "$POOL" --project "$PROJECT" --region "$REGION" \
      --format='value(metadata.name,metadata.annotations."run.googleapis.com/manualInstanceCount")'
    ;;
  *)
    echo "usage: $0 down|up|status" >&2
    exit 2
    ;;
esac
