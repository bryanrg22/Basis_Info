#!/usr/bin/env bash
# Ingest the reference corpus into the staging database: one execution of the
# staging-basis-ingest Cloud Run job over every corpus/<doc_id>.pdf in the private bucket
# (mounted read-only at /mnt/private), as `python -m basis.retrieval.ingest --pdf ...
# --doc-id ...`. The job publishes a new reference corpus version; the worker loads the
# latest published version's BM25 index at startup, so restart it afterwards.
#
# Then lists the corpus versions (a second, short execution) to show the result.
set -euo pipefail

PROJECT=${PROJECT:-basis-staging-512000}
REGION=${REGION:-us-central1}
BUCKET=${BUCKET:-staging-basis-staging-512000-private}
JOB=${JOB:-staging-basis-ingest}

args=""
for uri in $(gcloud storage ls "gs://$BUCKET/corpus/*.pdf" --project "$PROJECT"); do
  name=$(basename "$uri")
  args+="--pdf,/mnt/private/corpus/$name,--doc-id,${name%.pdf},"
done
[ -n "$args" ] || { echo "no PDFs under gs://$BUCKET/corpus/ (run upload-private.sh)"; exit 1; }

gcloud run jobs execute "$JOB" --project "$PROJECT" --region "$REGION" --wait \
  --args="${args%,}"
gcloud run jobs execute "$JOB" --project "$PROJECT" --region "$REGION" --wait \
  --args="--list-versions"
echo "Read the versions in the job's logs (Cloud Logging, resource cloud_run_job $JOB)."
