#!/usr/bin/env bash
# Deploy Gemini Live PTT to Cloud Run.
#
# Idempotent: re-runs cleanly on an already-deployed project (just builds a
# new revision). Performs one-time setup (enable APIs, create Secret Manager
# secret, grant IAM) every run; each step is a no-op when already done.
#
# Required:
#   GCP_PROJECT or `gcloud config set project <id>`
#   GOOGLE_API_KEY env var, or GOOGLE_API_KEY=... in .env
#
# Optional overrides:
#   GCP_REGION   (default: us-central1)
#   SERVICE      (default: gemini-live-ptt)
#   SECRET       (default: gemini-api-key)

set -euo pipefail

PROJECT="${GCP_PROJECT:-$(gcloud config get-value project 2>/dev/null || true)}"
REGION="${GCP_REGION:-us-central1}"
SERVICE="${SERVICE:-gemini-live-ptt}"
SECRET="${SECRET:-gemini-api-key}"

# ---- Pre-flight ---------------------------------------------------------

command -v gcloud >/dev/null \
  || { echo "ERROR: gcloud CLI not found. Install: https://cloud.google.com/sdk/docs/install" >&2; exit 1; }

if [ -z "${PROJECT:-}" ]; then
  echo "ERROR: project not set. Either:" >&2
  echo "  export GCP_PROJECT=your-project-id" >&2
  echo "  OR: gcloud config set project your-project-id" >&2
  exit 1
fi

if ! gcloud projects describe "$PROJECT" >/dev/null 2>&1; then
  echo "ERROR: cannot access project '$PROJECT'. Run 'gcloud auth login' or check the ID." >&2
  exit 1
fi

if [ -z "${GOOGLE_API_KEY:-}" ] && [ -f .env ]; then
  GOOGLE_API_KEY="$(grep '^GOOGLE_API_KEY=' .env | cut -d= -f2- || true)"
fi
if [ -z "${GOOGLE_API_KEY:-}" ]; then
  echo "ERROR: GOOGLE_API_KEY not found." >&2
  echo "  Either: export GOOGLE_API_KEY=AIza..." >&2
  echo "  Or:     add GOOGLE_API_KEY=AIza... to .env" >&2
  echo "  Get one at: https://aistudio.google.com/app/apikey" >&2
  exit 1
fi

echo "Project: $PROJECT"
echo "Region:  $REGION"
echo "Service: $SERVICE"
echo "Secret:  $SECRET"
echo "API key: loaded (length=${#GOOGLE_API_KEY})"
echo

# ---- 1. Enable APIs (idempotent) ---------------------------------------

echo "==> Enabling required APIs..."
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  --project="$PROJECT" \
  --quiet

# ---- 2. Create or update the Secret Manager secret ---------------------

echo "==> Storing API key in Secret Manager ($SECRET)..."
if gcloud secrets describe "$SECRET" --project="$PROJECT" >/dev/null 2>&1; then
  printf '%s' "$GOOGLE_API_KEY" | gcloud secrets versions add "$SECRET" \
    --project="$PROJECT" --data-file=- --quiet
else
  printf '%s' "$GOOGLE_API_KEY" | gcloud secrets create "$SECRET" \
    --project="$PROJECT" --replication-policy=automatic --data-file=- --quiet
fi

# ---- 3. Grant Secret Accessor to the Cloud Run runtime SA --------------

echo "==> Granting roles/secretmanager.secretAccessor to runtime SA..."
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')"
RUNTIME_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
gcloud secrets add-iam-policy-binding "$SECRET" \
  --project="$PROJECT" \
  --member="serviceAccount:${RUNTIME_SA}" \
  --role=roles/secretmanager.secretAccessor \
  --quiet >/dev/null

# ---- 4. Deploy ---------------------------------------------------------

echo "==> Deploying to Cloud Run (build runs in Cloud Build, 3-5 min first time)..."
gcloud run deploy "$SERVICE" \
  --project="$PROJECT" \
  --region="$REGION" \
  --source=. \
  --allow-unauthenticated \
  --port=8080 \
  --timeout=3600 \
  --no-cpu-throttling \
  --session-affinity \
  --set-secrets="GOOGLE_API_KEY=${SECRET}:latest" \
  --max-instances=5 \
  --min-instances=0 \
  --quiet

URL="$(gcloud run services describe "$SERVICE" \
  --project="$PROJECT" --region="$REGION" \
  --format='value(status.url)')"

echo
echo "==============================================================================="
echo "Deployed: $URL"
echo "Logs:     gcloud run services logs tail $SERVICE --project=$PROJECT --region=$REGION"
echo "Redeploy: ./deploy.sh"
echo "Tear down: gcloud run services delete $SERVICE --project=$PROJECT --region=$REGION"
echo "==============================================================================="
