# Cloud Run Deploy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deploy the existing Gemini Live PTT app to Cloud Run in `bigquery-demo-396708`, with Secret Manager for the API key and the WebSocket-friendly flag set.

**Architecture:** Custom `Dockerfile` (slim Python + uvicorn) + `gcloud run deploy --source .`. One-time GCP setup creates the secret and grants IAM; the deploy command applies the WS-friendly flags (`--timeout=3600`, `--no-cpu-throttling`, `--session-affinity`).

**Tech Stack:** Cloud Run, Cloud Build, Artifact Registry, Secret Manager, gcloud CLI, Docker (built remotely by Cloud Build).

**Spec:** `docs/superpowers/specs/2026-05-14-cloud-run-deploy-design.md`

---

## File Structure

| File | Purpose | Created in task |
|---|---|---|
| `Dockerfile` | Slim Python image, uvicorn entrypoint | 1 |
| `.dockerignore` | Exclude venv/git/env/docs/tests from build context | 1 |

No application-code changes. The other tasks are operational (GCP setup + deploy + verify) and don't touch the repo.

---

## Task 1: Add `Dockerfile` and `.dockerignore`

**Files:**
- Create: `Dockerfile`
- Create: `.dockerignore`

- [ ] **Step 1: Create `Dockerfile`**

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# Install deps before copying app source so the layer caches when only code changes.
COPY pyproject.toml ./
RUN pip install --no-cache-dir .

# Copy app source. .dockerignore excludes everything we don't need.
COPY app ./app

ENV PORT=8080
EXPOSE 8080

# Single uvicorn worker: each WS pins state to one process; Cloud Run scales horizontally.
CMD exec uvicorn app.server:app --host 0.0.0.0 --port "$PORT" --workers 1 --no-access-log
```

- [ ] **Step 2: Create `.dockerignore`**

```
.venv/
.git/
.gitignore
.env
.env.example
docs/
tests/
__pycache__/
*.pyc
.pytest_cache/
*.egg-info/
.github/
README.md
```

- [ ] **Step 3: Verify the build context is small**

Run:
```bash
tar --exclude-from=.dockerignore --exclude-vcs -cf - . 2>/dev/null | wc -c
```

Expected: under 1 MB (just the app source). If larger, check `.dockerignore` covers the heavy stuff.

- [ ] **Step 4: Verify the Dockerfile parses (syntax check only — no actual build)**

Run:
```bash
docker --version 2>/dev/null && (docker build --check . 2>&1 | head -5) || echo "docker not available locally — fine; Cloud Build will build remotely"
```

Either output is acceptable. We don't need a local Docker daemon — Cloud Build runs the actual build remotely.

- [ ] **Step 5: Commit**

```bash
git add Dockerfile .dockerignore
git -c commit.gpgsign=false -c user.email=claude@anthropic.com -c user.name=Claude commit -m "$(cat <<'EOF'
feat: add Dockerfile + .dockerignore for Cloud Run deploy

Slim Python 3.12 base, uvicorn entrypoint with single worker (per-WS
state stays in one process; Cloud Run handles horizontal scale).
.dockerignore excludes venv, docs, tests, secrets, CI from the build
context.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: One-time GCP setup

**Files:** None (operational only).

This task is idempotent — re-running on an already-set-up project succeeds and changes nothing.

- [ ] **Step 1: Enable required APIs**

Run:
```bash
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  --project=bigquery-demo-396708
```

Expected: each API enables (or reports "already enabled" — both fine). Total time ~30 s.

- [ ] **Step 2: Stage the API key into a shell variable**

The key already lives in `.env` from the local-smoke setup. Extract it without echoing:

```bash
export GOOGLE_API_KEY=$(grep '^GOOGLE_API_KEY=' .env | cut -d= -f2-)
[ -n "$GOOGLE_API_KEY" ] && echo "loaded (length=${#GOOGLE_API_KEY})" || echo "FAILED to load"
```

Expected: `loaded (length=39)` (Google AI Studio keys are 39 chars). Do NOT echo `$GOOGLE_API_KEY` directly.

- [ ] **Step 3: Create the Secret Manager secret**

```bash
if gcloud secrets describe gemini-api-key --project=bigquery-demo-396708 >/dev/null 2>&1; then
  echo "secret exists; adding new version"
  printf '%s' "$GOOGLE_API_KEY" | gcloud secrets versions add gemini-api-key \
    --project=bigquery-demo-396708 --data-file=-
else
  echo "creating new secret"
  printf '%s' "$GOOGLE_API_KEY" | gcloud secrets create gemini-api-key \
    --project=bigquery-demo-396708 --replication-policy=automatic --data-file=-
fi
```

Expected: either `created version [N]` or `Created secret [gemini-api-key]`. No key value should appear in the output.

- [ ] **Step 4: Grant `roles/secretmanager.secretAccessor` to the Cloud Run runtime SA**

```bash
PROJECT_NUMBER=$(gcloud projects describe bigquery-demo-396708 --format='value(projectNumber)')
gcloud secrets add-iam-policy-binding gemini-api-key \
  --project=bigquery-demo-396708 \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role=roles/secretmanager.secretAccessor
```

Expected: `Updated IAM policy for secret [gemini-api-key].` (or `bindings unchanged` if already granted).

- [ ] **Step 5: Verify the secret is readable by the runtime SA**

```bash
gcloud secrets get-iam-policy gemini-api-key \
  --project=bigquery-demo-396708 \
  --format='value(bindings.members)' | grep -q "compute@developer.gserviceaccount.com" \
  && echo "OK" || echo "MISSING"
```

Expected: `OK`.

No commit — this task touches no repo files.

---

## Task 3: Deploy

**Files:** None (operational only).

- [ ] **Step 1: Run `gcloud run deploy --source .`**

```bash
gcloud run deploy gemini-live-ptt \
  --project=bigquery-demo-396708 \
  --region=us-central1 \
  --source=. \
  --allow-unauthenticated \
  --port=8080 \
  --timeout=3600 \
  --no-cpu-throttling \
  --session-affinity \
  --set-secrets=GOOGLE_API_KEY=gemini-api-key:latest \
  --max-instances=5 \
  --min-instances=0
```

Expected (in order):
1. Cloud Build uploads the source (~tens of KB after `.dockerignore`).
2. Cloud Build runs the Dockerfile (3-5 min the first time; faster on rebuilds with layer caching).
3. Cloud Run rolls out a new revision and routes 100% traffic.
4. Final line: `Service URL: https://gemini-live-ptt-xxxxxxxxxx-uc.a.run.app`.

If gcloud asks to create an Artifact Registry repository, accept (`Y`).

If the build fails, capture the build logs URL from the gcloud output and read them — the most common failure mode is a missing dep or syntax error in the Dockerfile.

- [ ] **Step 2: Capture the service URL**

```bash
URL=$(gcloud run services describe gemini-live-ptt \
  --project=bigquery-demo-396708 --region=us-central1 \
  --format='value(status.url)')
echo "$URL"
```

Expected: an `https://...run.app` URL. Save this for Task 4.

No commit — operational only.

---

## Task 4: Verify the deploy

**Files:** None.

- [ ] **Step 1: HTTP smoke**

```bash
URL=$(gcloud run services describe gemini-live-ptt \
  --project=bigquery-demo-396708 --region=us-central1 \
  --format='value(status.url)')

echo "GET $URL/"
curl -s -o /dev/null -w "  status=%{http_code} time=%{time_total}s\n" "$URL/"

echo "GET $URL/static/ptt.js"
curl -s -o /dev/null -w "  status=%{http_code}\n" "$URL/static/ptt.js"

echo "GET $URL/static/player.js"
curl -s -o /dev/null -w "  status=%{http_code}\n" "$URL/static/player.js"

echo "GET $URL/static/recorder-worklet.js"
curl -s -o /dev/null -w "  status=%{http_code}\n" "$URL/static/recorder-worklet.js"
```

Expected: all four return `200`. The first request (status check on `/`) may be slow (5-10 s) due to cold start; subsequent requests should be <1 s.

- [ ] **Step 2: WebSocket handshake check**

```bash
URL=$(gcloud run services describe gemini-live-ptt \
  --project=bigquery-demo-396708 --region=us-central1 \
  --format='value(status.url)')
WS_URL="${URL/https:/wss:}/ws"
echo "WS endpoint: $WS_URL"
```

Verify the upgrade works using `curl` (since we don't have a CLI WS client guaranteed):

```bash
curl -i -N --http1.1 \
  -H "Connection: Upgrade" \
  -H "Upgrade: websocket" \
  -H "Sec-WebSocket-Version: 13" \
  -H "Sec-WebSocket-Key: $(printf 'cloud-run-test-key' | base64)" \
  "${URL}/ws" 2>&1 | head -10
```

Expected first line: `HTTP/1.1 101 Switching Protocols`. If you see `426 Upgrade Required` or `200`, something's wrong with the WS routing.

(After the handshake succeeds the connection sits idle; ctrl-C is fine.)

- [ ] **Step 3: Browser smoke**

Open the URL in Chrome:
```bash
echo "Open: $URL"
```

Run smoke check #1 (Connect) and #2 (First turn) from `tests/SMOKE.md`. If those pass, the deploy is healthy.

- [ ] **Step 4: Tail logs while smoke testing (optional but recommended)**

In a separate terminal:
```bash
gcloud run services logs tail gemini-live-ptt \
  --project=bigquery-demo-396708 --region=us-central1
```

Look for:
- `ptt server starting; model=gemini-3.1-flash-live-preview` (startup OK)
- `ws connected` / `ws closed` (per session)
- `bridge task error: ...` warnings (any unexpected exception in production)

No commit — operational only.

---

## Final verification

- [ ] **Step 1: Confirm the deploy is reachable and serves all assets**

Already covered by Task 4 step 1.

- [ ] **Step 2: Confirm the user has the URL and the smoke results**

Print the URL and a one-liner summary so the user can pick up from here:

```bash
URL=$(gcloud run services describe gemini-live-ptt \
  --project=bigquery-demo-396708 --region=us-central1 \
  --format='value(status.url)')
echo "Deployed: $URL"
echo "Logs:     gcloud run services logs tail gemini-live-ptt --project=bigquery-demo-396708 --region=us-central1"
echo "Redeploy: gcloud run deploy gemini-live-ptt --project=bigquery-demo-396708 --region=us-central1 --source=."
```

- [ ] **Step 3: Optionally tear down**

If the user no longer wants the service running:

```bash
gcloud run services delete gemini-live-ptt \
  --project=bigquery-demo-396708 --region=us-central1 --quiet
```

(The Secret Manager secret and IAM bindings persist for future redeploys; delete with `gcloud secrets delete gemini-api-key --project=bigquery-demo-396708` if you want them gone too.)
