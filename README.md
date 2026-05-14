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

## Design & plan

- Spec: [`docs/superpowers/specs/2026-05-14-gemini-live-ptt-design.md`](docs/superpowers/specs/2026-05-14-gemini-live-ptt-design.md)
- Plan: [`docs/superpowers/plans/2026-05-14-gemini-live-ptt.md`](docs/superpowers/plans/2026-05-14-gemini-live-ptt.md)
