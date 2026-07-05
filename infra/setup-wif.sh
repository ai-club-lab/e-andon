#!/usr/bin/env bash
#
# One-time Workload Identity Federation setup so GitHub Actions can deploy to
# Cloud Run WITHOUT any service-account key (keyless / OIDC).
#
# RUN AS a project OWNER/ADMIN of fhack26-aiclub. It needs a role the normal
# deployer does NOT have: roles/iam.workloadIdentityPoolAdmin (pool/provider
# creation). Everything else (SA-level bindings) is already in place.
#
# After it finishes, the `deploy` workflow activates automatically (it sets the
# GitHub repo variables it checks for). Requires: gcloud + gh CLIs, authenticated.
set -euo pipefail

PROJECT=fhack26-aiclub
PNUM=523085315022
REPO=ai-club-lab/chokotei-sentinel
SA="523085315022-compute@developer.gserviceaccount.com"   # runtime SA (has roles/editor); reused as deployer
POOL=github-pool
PROVIDER=github-provider

echo "1/3 Workload Identity Pool + GitHub OIDC provider…"
gcloud iam workload-identity-pools create "$POOL" \
  --project="$PROJECT" --location=global --display-name="GitHub Actions" || true
gcloud iam workload-identity-pools providers create-oidc "$PROVIDER" \
  --project="$PROJECT" --location=global --workload-identity-pool="$POOL" \
  --display-name="GitHub OIDC" \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.repository_owner=assertion.repository_owner" \
  --attribute-condition="assertion.repository_owner=='ai-club-lab'" || true

echo "2/3 Let the repo impersonate the deploy SA (keyless)…"
gcloud iam service-accounts add-iam-policy-binding "$SA" --project="$PROJECT" \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/${PNUM}/locations/global/workloadIdentityPools/${POOL}/attribute.repository/${REPO}"

echo "3/3 Set GitHub repo variables (activates the deploy workflow)…"
WIF="projects/${PNUM}/locations/global/workloadIdentityPools/${POOL}/providers/${PROVIDER}"
gh variable set WIF_PROVIDER  --repo "$REPO" --body "$WIF"
gh variable set DEPLOY_SA     --repo "$REPO" --body "$SA"
gh variable set GCP_PROJECT   --repo "$REPO" --body "$PROJECT"
gh variable set CLOUDSQL_CONN --repo "$REPO" --body "${PROJECT}:asia-northeast1:chokotei-db"

echo "Done. Next push to main (or: gh workflow run deploy) will deploy keyless."
