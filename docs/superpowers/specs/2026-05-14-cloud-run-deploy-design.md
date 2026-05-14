# Cloud Run Deploy — Design Spec

**Date:** 2026-05-14
**Status:** Draft, pending review
**Scope:** Deploy the existing Gemini Live PTT app to Cloud Run in project `bigquery-demo-396708`.

---

## 1. Problem & context

The app works locally (verified via `tests/SMOKE.md`). We want a publicly reachable URL so it can be demoed without running uvicorn locally. Cloud Run is the right primitive: managed, scale-to-zero, native HTTPS termination, supports WebSockets when configured correctly.

Two things make WebSockets on Cloud Run different from a normal HTTP service:

1. The default 5-minute request timeout will silently drop long-running PTT conversations.
2. Cloud Run's CPU throttling between requests will pause the per-connection bridge coroutines and stall audio/transcript flow.

The deploy must address both, plus deliver the `GOOGLE_API_KEY` securely and bound the blast radius of a public URL.

## 2. Requirements

| # | Requirement |
|---|---|
| R1 | Single Cloud Run service `gemini-live-ptt` in project `bigquery-demo-396708`, region `us-central1`. |
| R2 | Public access (`--allow-unauthenticated`). |
| R3 | `GOOGLE_API_KEY` delivered via Secret Manager (`gemini-api-key:latest`). |
| R4 | min instances = 0 (cost), max instances = 5 (cost cap + abuse cap). |
| R5 | WebSockets must work end-to-end without dropping mid-conversation. |
| R6 | TLS terminated at Cloud Run; browser uses `wss://` automatically. |
| R7 | No application-code changes — deploy reuses the working app. |
| R8 | Reproducible: a single `gcloud run deploy --source .` rebuilds and redeploys. |

## 3. Approach

`gcloud run deploy --source .` with a custom `Dockerfile` (rather than buildpacks). Buildpacks would also work but default to gunicorn, which has no native WebSocket support. A small explicit Dockerfile lets us run uvicorn directly, pin the Python version, and use a slim base for faster cold starts.

## 4. Files added (2)

### `Dockerfile`
```dockerfile
FROM python:3.12-slim

WORKDIR /app

# Install deps before copying code so layer caches when only code changes.
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e .

# Copy app source. .dockerignore excludes everything we don't need.
COPY app ./app

ENV PORT=8080
EXPOSE 8080

# Single uvicorn worker: each WS pins state to one process; Cloud Run scales horizontally.
CMD exec uvicorn app.server:app --host 0.0.0.0 --port "$PORT" --workers 1 --no-access-log
```

### `.dockerignore`
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

No application-code changes.

## 5. One-time GCP setup

Each step is idempotent — re-running on an already-set-up project is a no-op.

1. **Enable required APIs:**
   ```
   gcloud services enable \
     run.googleapis.com \
     cloudbuild.googleapis.com \
     artifactregistry.googleapis.com \
     secretmanager.googleapis.com \
     --project=bigquery-demo-396708
   ```

2. **Create the secret:**
   ```
   printf '%s' "$GOOGLE_API_KEY" | \
     gcloud secrets create gemini-api-key \
       --project=bigquery-demo-396708 \
       --replication-policy=automatic \
       --data-file=-
   ```
   (If the secret already exists, use `gcloud secrets versions add gemini-api-key --data-file=-` instead.)

3. **Grant Secret Manager Secret Accessor to the Cloud Run runtime SA:**
   ```
   PROJECT_NUMBER=$(gcloud projects describe bigquery-demo-396708 --format='value(projectNumber)')
   gcloud secrets add-iam-policy-binding gemini-api-key \
     --project=bigquery-demo-396708 \
     --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
     --role=roles/secretmanager.secretAccessor
   ```

## 6. Deploy

```
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

**Why each non-default flag:**

| Flag | Reason |
|---|---|
| `--timeout=3600` | Default is 300 s (5 min). PTT conversations can run longer; would otherwise be silently dropped. 3600 s is the Cloud Run gen2 maximum. |
| `--no-cpu-throttling` | CPU is normally throttled between requests; the per-connection bridge coroutines need continuous CPU to keep audio/transcript flowing. |
| `--session-affinity` | Best-effort routing of repeat requests from the same client to the same instance. Important so a brief WS reconnect lands on the same container (preserves ADK session). |
| `--allow-unauthenticated` | R2: public access. |
| `--port=8080` | Cloud Run injects `PORT=8080` by default; explicit for clarity. |
| `--max-instances=5` | R4: cost cap and soft brake against API-key abuse on a public URL. |
| `--min-instances=0` | R4: scale to zero when idle. Cold starts ~5-10 s acceptable for demo. |
| `--set-secrets=...` | R3: mount the API key as an env var sourced from Secret Manager. |

## 7. Verification

1. **URL:**
   ```
   URL=$(gcloud run services describe gemini-live-ptt \
     --project=bigquery-demo-396708 --region=us-central1 \
     --format='value(status.url)')
   echo "$URL"
   ```
2. **HTTP check:** `curl -s -o /dev/null -w '%{http_code}\n' "$URL/"` → expect `200`.
3. **WebSocket protocol check:** open browser dev tools, load `$URL`, watch Network tab for the `/ws` upgrade — should be `101 Switching Protocols` over `wss://`.
4. **End-to-end:** run smoke checks #1 (Connect) and #2 (First turn) from `tests/SMOKE.md`. If those pass, the cloud deploy works.

## 8. Risks (acknowledged, not mitigated)

- **Cold start ~5-10 s** for the first request after idle. `google-adk` pulls heavy GCP deps. Acceptable for demo; mitigated by min=1 only if the demo is frequent enough to justify ~$10-25/month.
- **Public URL costs/quota.** Anyone with the URL spends the project's Gemini quota. `--max-instances=5` caps concurrency; rotate the key (or switch to authed access) after the demo if exposure is a concern.
- **Session affinity is best-effort.** A reconnect MAY land on a different instance; the existing client behavior (drop pending press, require re-press) handles this gracefully.
- **Input-transcript open question** carries over from local: the existing replace-vs-append behavior in `ptt.js` is the same; verify in cloud smoke test the same way.

## 9. Out of scope

- Authenticated access (IAP / Cloud Run Invoker IAM).
- Custom domain.
- Per-tenant API keys / cost attribution.
- Observability beyond default Cloud Run logs (no Cloud Trace, no metrics export).
- Multi-region failover.
- Blue/green or canary deploys.
- Container image scanning beyond defaults.

## 10. Out-of-band test

Smoke-test by hitting the URL after deploy completes. No automated cloud test (would need a browser + mic, same constraints as local smoke).
