# Gemini Live Push-to-Talk — Design Spec

**Date:** 2026-05-14
**Status:** Draft, pending review
**Scope:** Single-user local test app for push-to-talk voice conversations with Gemini Live via Google ADK.

---

## 1. Problem & context

Gemini Live's default behavior uses server-side Voice Activity Detection (VAD) to infer turn boundaries from audio energy and silence. For a true push-to-talk (PTT) UX — where the user explicitly signals when they are speaking by holding a key — automatic VAD causes two failure modes:

1. **False interruptions.** Background noise during silence reaches the model and is interpreted as the user starting to speak, which interrupts the model's response.
2. **Premature turn-end / late turn-end.** Silence inside an utterance is treated as turn-complete; conversely, ambient noise after the user stops keeps the turn open.

The fix is to **disable automatic VAD** and use Gemini Live's manual activity detection mode, where the client owns turn boundaries by sending explicit `activity_start` / `activity_end` signals.

ADK exposes this via:
- `RunConfig.realtime_input_config = RealtimeInputConfig(automatic_activity_detection=AutomaticActivityDetection(disabled=True))`
- `LiveRequestQueue.send_activity_start()` and `LiveRequestQueue.send_activity_end()`
- `LiveRequestQueue.send_realtime(blob)` for the audio frames between them

This spec describes a small Python + ADK + FastAPI app with a vanilla HTML/JS frontend that exercises this end-to-end.

## 2. Requirements

**Confirmed during brainstorming:**

| # | Requirement |
|---|---|
| R1 | PTT trigger is **hold-to-talk via spacebar** in the browser. |
| R2 | Response is **audio playback** with **transcript displayed alongside**. |
| R3 | Architecture is **custom FastAPI + WebSocket + plain HTML/JS** (no ADK web UI, no SSE). |
| R4 | Mid-response keypress = **cancel the model's response, start a new user turn**. |
| R5 | Auth via **Google AI Studio API key** (`GOOGLE_API_KEY`). |
| R6 | Use latest Gemini Live preview model in **ADK v1.x** (not 2.0). |
| R7 | Mute/unmute must NOT trigger false interruptions or unfinished turns — accomplished via manual VAD + browser-side audio gating. |

**Implicit/derived:**

| # | Requirement |
|---|---|
| R8 | Single-user local test app (no auth, no multi-tenancy). |
| R9 | In-memory session per WS connection (no persistence). |
| R10 | No tools / function calling in v1 (chat only). |
| R11 | Browser audio: 16 kHz mono PCM up; 24 kHz mono PCM down. |

## 3. Architecture

```
┌──────────────────────────┐         WebSocket          ┌──────────────────────────┐         google-genai        ┌──────────────────┐
│  Browser (index.html)    │  ───── /ws audio frames ─► │  FastAPI server (main.py)│  ───── run_live() ────────► │ Gemini Live API  │
│                          │  ◄──── /ws audio + text ── │                          │  ◄───── async events ────── │ (preview model)  │
│ • PTT spacebar handler   │                            │ • WS endpoint            │                             └──────────────────┘
│ • AudioWorklet capture   │                            │ • ADK Runner per session │
│ • PCM playback queue     │                            │ • LiveRequestQueue       │
│ • Transcript renderer    │                            │ • Bridge tasks (2)       │
└──────────────────────────┘                            └──────────────────────────┘
```

**Mute gate location: browser-side.** The mic AudioWorklet only emits PCM frames while the spacebar is held; no audio crosses the WS between presses. Combined with manual VAD on the server, this guarantees no false interruptions.

### 3.1 File layout

| File | Purpose | Approx LOC |
|---|---|---|
| `app/agent.py` | `LlmAgent` definition: name, model, system instruction. | ~15 |
| `app/server.py` | FastAPI app, `/` static handler, `/ws` WebSocket endpoint, ADK runner setup. | ~120 |
| `app/bridge.py` | Two coroutines: `browser_to_adk(ws, queue, state)` and `adk_to_browser(ws, live_events, state)`. Decouples WS protocol from ADK plumbing. | ~80 |
| `app/static/index.html` | Single page: status pill, transcript pane, "Hold SPACE to talk" hint. | ~60 |
| `app/static/ptt.js` | Keydown/keyup, AudioWorklet setup, WS client, downlink playback. | ~150 |
| `app/static/recorder-worklet.js` | AudioWorklet processor: float32 → 16-bit PCM @ 16 kHz. | ~25 |
| `app/static/player.js` | PCM @ 24 kHz playback queue using chained `AudioBufferSourceNode`s. | ~50 |
| `pyproject.toml` | Deps: `google-adk`, `google-genai`, `fastapi`, `uvicorn[standard]`, `python-dotenv`. | — |
| `.env.example` | `GOOGLE_API_KEY=...` | — |
| `tests/` | Pytest suite (see §7). | — |

**Boundary rationale.** `agent.py` knows LLM concerns only. `bridge.py` knows ADK plumbing but takes thin sender/receiver interfaces — no WebSocket framing details. `server.py` wires them. The browser splits capture (worklet) from session control (`ptt.js`) from playback (`player.js`) so each piece is independently swappable.

## 4. Wire protocol & data flow

### 4.1 Framing

Mixed text/binary on a single WebSocket:

- **Binary frames** = raw PCM audio. Direction determines sample rate (browser→server: 16 kHz; server→browser: 24 kHz). No header, no base64. The WS opcode (binary) discriminates from JSON.
- **Text frames** = JSON control + transcript messages. The `type` field discriminates.

### 4.2 Browser → server messages

| Message | Trigger |
|---|---|
| binary PCM frame (~20 ms, 640 bytes @ 16 kHz mono) | While spacebar held |
| `{"type": "speech_start"}` | Spacebar `keydown` |
| `{"type": "speech_end"}` | Spacebar `keyup` + 200 ms grace timer |
| `{"type": "barge_in"}` | Spacebar `keydown` while model is mid-response (sent BEFORE `speech_start`) |

### 4.3 Server → browser messages

| Message | Source |
|---|---|
| binary PCM frame | Each audio chunk in the model's response |
| `{"type": "input_transcript",  "text": str, "final": bool}` | ADK `input_transcription` event |
| `{"type": "output_transcript", "text": str, "final": bool}` | ADK `output_transcription` event |
| `{"type": "turn_complete"}` | Model finished its turn |
| `{"type": "interrupted"}` | Server confirmation that barge-in took effect |
| `{"type": "error", "message": str}` | Server-side error surfaced to UI |

### 4.4 Happy-path sequence (one PTT turn)

```
Browser                                        Server                                       Gemini Live
───────                                        ──────                                       ───────────
[user presses SPACE]
  ─── text {"type":"speech_start"} ────────────►
                                               live_queue.send_activity_start()  ─────────►
  ─── binary PCM frame ────────────────────────►
                                               live_queue.send_realtime(blob)    ─────────►
  ... (frames flow at ~50/sec while held) ...
[user releases SPACE]
  [200 ms grace timer]
  ─── text {"type":"speech_end"} ──────────────►
                                               live_queue.send_activity_end()    ─────────►
                                                                                  ◄──── input_transcription event
  ◄── text {"input_transcript", final:true} ───
                                                                                  ◄──── audio response chunk
  ◄── binary PCM ──────────────────────────────
  [enqueue + play via AudioBufferSourceNode]
                                                                                  ◄──── output_transcription event
  ◄── text {"output_transcript", final:false}──
  ... (audio + transcript chunks interleave) ...
                                                                                  ◄──── turn_complete event
  ◄── text {"turn_complete"} ──────────────────
```

### 4.5 Key timing & framing details

- **Frame size up.** AudioWorklet runs in 128-sample blocks; the recorder accumulates to 320 samples (~20 ms @ 16 kHz) before posting to main thread, which then sends as a single binary WS frame. Result: ~50 messages/sec uplink.
- **200 ms grace.** After `keyup`, the worklet keeps emitting frames for 200 ms before the main thread sends `speech_end`. Avoids clipping the last word. Google's docs recommend ≥500 ms for natural-pause-tolerant flows; 200 ms is sufficient for a deliberate PTT gesture and feels snappier. Configurable constant (`SPEECH_END_GRACE_MS`) in `ptt.js`.
- **Bridge ordering.** `browser_to_adk` forwards each WS message synchronously without batching. ADK's `LiveRequestQueue` priority order (`activity_start > activity_end > blob > content`) provides additional safety.
- **Echo cancellation.** Rely on browser default `getUserMedia({audio:{echoCancellation:true}})`. Headphones recommended for testing.

## 5. Interruption (barge-in) handling

### 5.1 Browser side (`ptt.js`)

On spacebar `keydown` while audio is playing in `player.js`'s queue:

1. **Immediately** call `player.flush()`: drop every queued `AudioBufferSourceNode` and stop the active one. The user must hear silence within one audio frame (~21 ms @ 24 kHz).
2. Send `{"type":"barge_in"}` text frame **before** `{"type":"speech_start"}`. Two messages, in order.
3. Continue normal PTT flow: `speech_start` → PCM frames → `speech_end`.

### 5.2 Server side (`bridge.browser_to_adk`)

```python
match msg["type"]:
    case "barge_in":
        # ADK's run_live() honors a fresh activity_start as an implicit
        # interruption when manual VAD is disabled, but we ALSO set a
        # bridge-local flag so adk_to_browser drops any in-flight audio
        # chunks the model already emitted before the interruption
        # propagates. Without this, the user gets ~100-300ms of stale
        # audio after they barged in.
        state.interrupting = True
    case "speech_start":
        live_queue.send_activity_start()
        state.interrupting = False
    case "speech_end":
        live_queue.send_activity_end()
```

### 5.3 Server side (`bridge.adk_to_browser`)

```python
async for event in live_events:
    if state.interrupting and (event_has_audio(event) or event_has_transcript(event)):
        continue  # drop stale events that were in-flight before barge-in
    # forward audio bytes as binary; transcripts/turn_complete/interrupted as JSON
```

The `state.interrupting` flag flips off the moment we receive the new `speech_start`, so events from the new turn flow through normally.

### 5.4 Server-side `interrupted` event handling

`LiveServerContent` events carry `interrupted: bool`. With manual VAD disabled, this fires when we send a fresh `activity_start` mid-turn. When received, forward as `{"type":"interrupted"}` to the browser as a confirmation marker (debugging UI; not load-bearing for correctness).

### 5.5 Edge cases handled

- **Mid-response keypress with both audio and transcript flowing.** Both are dropped while `interrupting` is true so the displayed transcript doesn't show a half-finished sentence as "complete".
- **Rapid press/release cycles.** Each cycle is its own `start`/`audio`/`end`. ADK's queue serializes them.
- **Spacebar held while WS reconnecting.** PCM frames buffered in browser; dropped if WS doesn't reopen within 1 s. Status pill shows "reconnecting…".

### 5.6 Explicit non-goals

- No client-side VAD to "verify" the user really spoke before sending `activity_start`. The spacebar IS the activity signal.
- No queueing of mid-response presses ("wait for model"). User ruled this out.
- No half-duplex lockout. Spacebar is always live, even during model speech.

## 6. Model, agent, and RunConfig

### 6.1 Model

**Default:** `gemini-3.1-flash-live-preview` — newer of the two preview Live models, low-latency A2A, voice-first.

**Alternative:** `gemini-2.5-flash-native-audio-preview-12-2025` — flagship native-audio model, supports affective dialog & proactivity. Not used for PTT v1 because those features are unused when VAD is disabled.

Pinned as a single constant `LIVE_MODEL` in `agent.py` so swapping is one-line. Comment notes that previews can be deprecated with as little as 2 weeks' notice.

### 6.2 Agent (`app/agent.py`)

```python
from google.adk.agents import LlmAgent

LIVE_MODEL = "gemini-3.1-flash-live-preview"

root_agent = LlmAgent(
    name="ptt_assistant",
    model=LIVE_MODEL,
    instruction=(
        "You are a concise voice assistant. Respond in 1-3 sentences unless "
        "the user asks for detail. Speak naturally; do not read out punctuation."
    ),
)
```

No tools in v1.

### 6.3 RunConfig (`app/server.py`)

```python
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.genai import types

run_config = RunConfig(
    streaming_mode=StreamingMode.BIDI,
    response_modalities=["AUDIO"],
    realtime_input_config=types.RealtimeInputConfig(
        automatic_activity_detection=types.AutomaticActivityDetection(
            disabled=True,  # PTT: client owns activity boundaries
        ),
    ),
    input_audio_transcription=types.AudioTranscriptionConfig(),
    output_audio_transcription=types.AudioTranscriptionConfig(),
    speech_config=types.SpeechConfig(
        voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Aoede"),
        ),
    ),
)
```

### 6.4 Per-connection wiring (in `/ws` handler)

```python
session = await session_service.create_session(app_name="ptt", user_id="local")
live_queue = LiveRequestQueue()
live_events = runner.run_live(
    session=session,
    live_request_queue=live_queue,
    run_config=run_config,
)
state = BridgeState()
async with asyncio.TaskGroup() as tg:
    tg.create_task(browser_to_adk(ws, live_queue, state))
    tg.create_task(adk_to_browser(ws, live_events, state))
```

`InMemorySessionService` is used. New session per WS connect; deleted on disconnect.

## 7. Error handling & lifecycle

### 7.1 Connection lifecycle (per WS)

```
WS open → create session → start run_live() → spawn bridges via TaskGroup
                                                    │
                                                    ├─ browser_to_adk: until WS close
                                                    └─ adk_to_browser: until live_events ends
WS close → live_queue.close() → session_service.delete_session() → TaskGroup cancels
```

Use `asyncio.TaskGroup` (Python ≥3.11) so cancellation propagates cleanly: if either bridge raises, the other is cancelled and the WS is closed with a code.

### 7.2 Failure matrix

| Failure | Where | Recovery |
|---|---|---|
| `getUserMedia` denied / no mic | Browser | Show overlay: "Microphone permission required." Disable spacebar. No retry — user must reload after granting. |
| WS connection lost mid-session | Browser | Reconnect with backoff (250 ms → 500 → 1000 → 2000, cap 5 s). Buffer up to 1 s of PCM during reconnect; drop older frames. Show "reconnecting…" pill. |
| WS connection lost server side | Server | `live_queue.close()` ends the bridges; session is discarded. Reconnect = new session. |
| Gemini API auth failure | Server (on first event) | Catch in `adk_to_browser`, send `{"type":"error","message":"auth_failed"}`, close WS. Browser shows banner. |
| Gemini transient / 5xx | Server | Surface as `error` text frame. Don't auto-retry the live session; let the user reconnect. |
| Audio playback underrun | Browser | Player keeps a 100 ms jitter buffer before starting playback for a turn; if buffer empties mid-turn, log to console and continue (no UI signal). |
| Worklet send while WS closed | Browser | Drop frame silently; spacebar handler short-circuits if `ws.readyState !== OPEN`. |
| User holds spacebar > 60 s | Browser | Hard cap: send `speech_end`, show toast "max turn length reached". |
| Tab backgrounded mid-press | Browser | On `visibilitychange` while hidden and pressed, treat as released and send `speech_end`. |

### 7.3 Out of scope for error handling

- Network jitter < 250 ms — TCP/WS handles it.
- Gemini Live "session timeout" (~10 min idle) — next press surfaces an error and prompts reconnect.
- Multi-tab sessions stepping on each other — out of scope.

### 7.4 Logging

Structured logs in `server.py` at INFO for connect/disconnect/turn-complete; WARNING for any forwarded error. Stdout only (uvicorn captures).

## 8. Testing strategy

### 8.1 Unit tests (pytest, async) — `tests/`

| Test | Asserts |
|---|---|
| `test_bridge_browser_to_adk_speech_start` | `{"type":"speech_start"}` → exactly one `send_activity_start()` call; `interrupting` flag cleared. |
| `test_bridge_browser_to_adk_audio_frame` | Binary frame → `send_realtime(blob)` with mime `audio/pcm;rate=16000`. |
| `test_bridge_browser_to_adk_speech_end` | `{"type":"speech_end"}` → exactly one `send_activity_end()` call. |
| `test_bridge_barge_in_drops_in_flight_audio` | After `barge_in`, audio events from a fake `live_events` are dropped until next `speech_start`. |
| `test_bridge_barge_in_drops_in_flight_transcript` | Same, for transcript events. |
| `test_bridge_forwards_output_audio_as_binary` | Audio event → WS receives binary frame matching the bytes. |
| `test_bridge_forwards_transcripts_as_json` | Transcription event → WS receives `{"type":"output_transcript", ...}`. |
| `test_run_config_disables_vad` | Construct `run_config`; assert `realtime_input_config.automatic_activity_detection.disabled is True`. Regression guard for the core PTT switch. |

`LiveRequestQueue` and `live_events` are mocked with `unittest.mock.AsyncMock` and small fake async iterators. No real Gemini calls in unit tests.

### 8.2 Manual smoke checklist (`tests/SMOKE.md`)

1. `uv run uvicorn app.server:app` → open `http://localhost:8000`.
2. Grant mic permission. Status pill shows "ready".
3. Hold spacebar, ask "Hello, what's two plus two?", release. Verify input transcript appears, model audio plays, output transcript shows incrementally, `turn_complete` indicator fires.
4. Hold spacebar mid-response. Verify audio cuts within ~50 ms, model starts a new turn from your latest input.
5. Tap-and-release very quickly (< 200 ms). Verify the model still gets the audio (grace period kept the tail).
6. Hold spacebar for 60 s of silence. Verify auto-end fires, toast shown.
7. Disconnect Wi-Fi briefly, reconnect. Verify status pill cycles "reconnecting…" → "ready", next press works.
8. Revoke mic permission, reload. Verify overlay tells user how to re-grant.

### 8.3 Out of scope for tests

- AudioWorklet PCM conversion correctness — covered by ear.
- WebSocket framing — Starlette's responsibility.
- Gemini response quality.

### 8.4 CI

Single GitHub Actions job: `pytest`. No browser e2e.

## 9. Out of scope (v1)

- Tool / function calling
- Multi-agent / sub-agents
- Vertex AI auth backend (API key only)
- Conversation persistence across page reloads
- Mobile / touch UI (spacebar required)
- Multi-user, multi-tab, auth, rate limiting
- Server-side max turn length (browser-side only at 60 s)
- Production deployment concerns (TLS, reverse proxy, etc.)

## 10. Open questions

None at spec time. Implementation will surface details around:
- Exact ADK event field names for `input_transcription` vs `output_transcription` (resolve by reading event objects during smoke test, then code accordingly).
- Whether ADK emits `interrupted` synchronously enough that the browser-side `interrupting` flag is needed in addition to the server-side flag — measured during smoke test 4.
