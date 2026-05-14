# Manual smoke test

Run end-to-end after any change touching the bridge, server, or frontend.

## Setup

1. `cp .env.example .env` and fill in `GOOGLE_API_KEY`.
2. `.venv/bin/uvicorn app.server:app --port 8000 --reload`
3. Open `http://localhost:8000` in Chrome (or any modern browser).
4. Wear headphones.

## Checks

1. **Connect.** Status pill goes from "connecting…" → "ready". No console errors.
2. **First turn.**
   - Hold spacebar, say *"Hello, what's two plus two?"*, release.
   - Expect: input transcript appears below, model audio plays back, output transcript shows incrementally, status returns to "ready" when done.
3. **Barge-in.**
   - Start a turn that prompts a long answer (e.g. *"Tell me a short story about a robot"*).
   - While the model is speaking, hold spacebar again.
   - Expect: model audio cuts within ~50 ms; new input transcript begins; new model response replaces the old one.
4. **Tap and release.**
   - Press spacebar very quickly (under 200 ms) while saying a single word.
   - Expect: model still receives the word (grace period kept the tail).
5. **Long press.**
   - Hold spacebar in silence for ~60 s.
   - Expect: status pill flips to "max turn length reached", press auto-ends.
6. **Network blip.**
   - Disable Wi-Fi for 5 s, re-enable.
   - Expect: status pill cycles "reconnecting…" → "ready". Next press works.
7. **Mic denied.**
   - Block mic in browser settings, reload.
   - Expect: dark overlay tells the user to grant permission.
