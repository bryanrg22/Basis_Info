#!/usr/bin/env bash
# Copy the deployment-only private files to the staging private bucket. They are licensed
# or client-adjacent and never enter the repository:
#
#   corpus/<doc_id>.pdf   the six reference PDFs (IRS ATG, Pub 527, Pub 946, Rev. Proc.
#                         87-56, RSMeans Building and Residential 2020)
#   costs/<table>.json    the RSMeans-derived unit-cost table (COST_TABLE_PATH)
#
# Sources default to the private eval data directory (~/basis-eval-data). `cp` with
# --no-clobber keeps re-runs cheap; pass FORCE=1 to replace objects (the bucket is
# versioned, so the previous table stays recoverable).
set -euo pipefail

PROJECT=${PROJECT:-basis-staging-512000}
BUCKET=${BUCKET:-staging-basis-staging-512000-private}
CORPUS_DIR=${CORPUS_DIR:-$HOME/basis-eval-data/corpus}
COST_TABLE=${COST_TABLE:-$HOME/basis-eval-data/costs/unit_costs_rsmeans_2020.json}

flags=(--project "$PROJECT")
[ "${FORCE:-0}" = 1 ] || flags+=(--no-clobber)

gcloud storage cp "${flags[@]}" "$CORPUS_DIR"/*.pdf "gs://$BUCKET/corpus/"
gcloud storage cp "${flags[@]}" "$COST_TABLE" "gs://$BUCKET/costs/$(basename "$COST_TABLE")"
gcloud storage ls -l "gs://$BUCKET/**" --project "$PROJECT"
