#!/usr/bin/env bash
# Create (or rotate) the application's database user and store its connection URL in
# Secret Manager (staging-database-url). The password is generated here, sent to Cloud SQL
# and to Secret Manager over stdin, and never printed, written to disk or put in Terraform.
#
# After a rotation, new Cloud Run instances read the new URL ("latest" version); redeploy
# (or restart the worker pool) so running instances pick it up.
set -euo pipefail

PROJECT=${PROJECT:-basis-staging-512000}
REGION=${REGION:-us-central1}
INSTANCE=${INSTANCE:-staging-basis-pg}
DB=${DB:-basis}
USER_NAME=${USER_NAME:-basis}
SECRET=${SECRET:-staging-database-url}

CONNECTION="$PROJECT:$REGION:$INSTANCE"
PASSWORD=$(openssl rand -hex 24) # hex: no character needs URL escaping

if gcloud sql users list --instance "$INSTANCE" --project "$PROJECT" --format='value(name)' \
  | grep -qx "$USER_NAME"; then
  gcloud sql users set-password "$USER_NAME" --instance "$INSTANCE" --project "$PROJECT" \
    --password "$PASSWORD" --quiet
else
  gcloud sql users create "$USER_NAME" --instance "$INSTANCE" --project "$PROJECT" \
    --password "$PASSWORD" --quiet
fi

# asyncpg over the Cloud Run Cloud SQL socket (/cloudsql/<connection>/.s.PGSQL.5432).
TEMPLATE='postgresql+asyncpg://%s:%s@/%s?host=/cloudsql/%s' # pragma: allowlist secret
# shellcheck disable=SC2059 # the template is ours
printf "$TEMPLATE" "$USER_NAME" "$PASSWORD" "$DB" "$CONNECTION" \
  | gcloud secrets versions add "$SECRET" --project "$PROJECT" --data-file=- --format='value(name)' \
  | sed 's#.*/versions/#'"$SECRET"' version #'
unset PASSWORD
