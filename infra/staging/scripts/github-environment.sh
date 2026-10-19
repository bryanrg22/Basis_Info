#!/usr/bin/env bash
# Configure the GitHub environment `staging` that the CI deploy job runs in.
#
# * Deployments only from the main branch: the Workload Identity binding trusts any job in
#   this environment (wif.tf), so the environment must not be usable from other branches.
# * REVIEWER=<github login> adds a required reviewer (a manual approval before each deploy);
#   without it every merge to main deploys.
# * Environment variables (not secrets: they are identifiers) for the deploy job.
set -euo pipefail

REPO=${REPO:-bryanrg22/Basis_Info}
ENVIRONMENT=staging
PROVIDER=${PROVIDER:-projects/138338018557/locations/global/workloadIdentityPools/github-actions/providers/github}
DEPLOYER=${DEPLOYER:-staging-deployer@basis-staging-512000.iam.gserviceaccount.com}

reviewers='[]'
if [ -n "${REVIEWER:-}" ]; then
  id=$(gh api "users/$REVIEWER" --jq .id)
  reviewers="[{\"type\":\"User\",\"id\":$id}]"
fi

gh api -X PUT "repos/$REPO/environments/$ENVIRONMENT" --input - <<EOF >/dev/null
{
  "reviewers": $reviewers,
  "deployment_branch_policy": {"protected_branches": false, "custom_branch_policies": true}
}
EOF

existing=$(gh api "repos/$REPO/environments/$ENVIRONMENT/deployment-branch-policies" \
  --jq '.branch_policies[].name')
if ! grep -qx main <<<"$existing"; then
  gh api -X POST "repos/$REPO/environments/$ENVIRONMENT/deployment-branch-policies" \
    -f name=main -f type=branch >/dev/null
fi

gh variable set GCP_WORKLOAD_IDENTITY_PROVIDER --repo "$REPO" --env "$ENVIRONMENT" --body "$PROVIDER"
gh variable set GCP_DEPLOYER_SERVICE_ACCOUNT --repo "$REPO" --env "$ENVIRONMENT" --body "$DEPLOYER"

gh api "repos/$REPO/environments/$ENVIRONMENT" \
  --jq '{name, protection_rules: [.protection_rules[].type], deployment_branch_policy}'
gh api "repos/$REPO/environments/$ENVIRONMENT/deployment-branch-policies" \
  --jq '[.branch_policies[].name]'
