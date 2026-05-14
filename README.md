# gemini-live-ptt

Single-user push-to-talk web app for Gemini Live, built on Google ADK.
Disables Gemini Live's automatic VAD so the spacebar is the sole turn-boundary signal.

## Quickstart

```bash
python3.11 -m venv .venv
.venv/bin/pip install -e '.[dev]'
cp .env.example .env  # then edit and add your GOOGLE_API_KEY
.venv/bin/uvicorn app.server:app --port 8000
```

Open http://localhost:8000 and hold **SPACE** to talk.

## Tests

```bash
.venv/bin/pytest -q
```

Manual smoke checklist: [`tests/SMOKE.md`](tests/SMOKE.md).

## Deploy to Cloud Run

[`deploy.sh`](deploy.sh) runs the full one-time setup plus deploy in a single
idempotent command. It enables the required APIs, stores `GOOGLE_API_KEY` in
Secret Manager, grants the Cloud Run runtime service account access to it,
then deploys with the WebSocket-friendly flags (`--timeout=3600`,
`--no-cpu-throttling`, `--session-affinity`).

**Prerequisites:**
- `gcloud` CLI installed and authenticated (`gcloud auth login`).
- A GCP project where you have Owner or Editor role.
- `GOOGLE_API_KEY` available either as an env var or in `.env`.

**Run:**

```bash
# Tell gcloud which project, either via config or env var:
gcloud config set project YOUR_PROJECT_ID
# OR: export GCP_PROJECT=YOUR_PROJECT_ID

./deploy.sh
```

The script prints the public service URL when it finishes. First run takes
3-5 min (Cloud Build pulls the base image and installs deps); subsequent
runs are 1-2 min thanks to layer caching.

**Optional overrides** (env vars):

| Variable | Default | Purpose |
|---|---|---|
| `GCP_PROJECT` | gcloud's active project | Target project. |
| `GCP_REGION` | `us-central1` | Cloud Run region. |
| `SERVICE` | `gemini-live-ptt` | Cloud Run service name. |
| `SECRET` | `gemini-api-key` | Secret Manager secret name. |
| `GOOGLE_API_KEY` | (read from `.env`) | API key value. Env var wins over `.env`. |

**Cost notes:**
- `--min-instances=0` means scale-to-zero; first request after idle is ~5-10 s cold.
- `--max-instances=5` caps concurrency to bound API costs on a public URL.
- Tear down: `gcloud run services delete $SERVICE --region=$GCP_REGION`.

**Design & deploy plan:**
- Spec: [`docs/superpowers/specs/2026-05-14-cloud-run-deploy-design.md`](docs/superpowers/specs/2026-05-14-cloud-run-deploy-design.md)
- Plan: [`docs/superpowers/plans/2026-05-14-cloud-run-deploy.md`](docs/superpowers/plans/2026-05-14-cloud-run-deploy.md)

## Design & plan (app)

- Spec: [`docs/superpowers/specs/2026-05-14-gemini-live-ptt-design.md`](docs/superpowers/specs/2026-05-14-gemini-live-ptt-design.md)
- Plan: [`docs/superpowers/plans/2026-05-14-gemini-live-ptt.md`](docs/superpowers/plans/2026-05-14-gemini-live-ptt.md)
