# Gemini Live Push-to-Talk Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a single-user push-to-talk web app that connects browser mic to Gemini Live via Google ADK with manual VAD disabled, so spacebar press/release defines turn boundaries cleanly.

**Architecture:** FastAPI server hosts a static HTML/JS page and a `/ws` WebSocket endpoint. The browser captures mic via AudioWorklet only while spacebar is held, sending PCM frames + `speech_start`/`speech_end` JSON control messages. Server bridges those into ADK's `LiveRequestQueue` (`send_activity_start` / `send_realtime` / `send_activity_end`) and forwards model events back as binary audio + JSON transcripts. ADK's `RunConfig.realtime_input_config.automatic_activity_detection.disabled=True` is the load-bearing config that prevents Gemini from inferring its own turn boundaries.

**Tech Stack:** Python 3.12+, `google-adk>=1.25,<2`, `google-genai`, FastAPI, Uvicorn, plain HTML/JS (no framework), AudioWorklet, pytest + pytest-asyncio. Auth via `GOOGLE_API_KEY` (Google AI Studio).

**Spec:** `docs/superpowers/specs/2026-05-14-gemini-live-ptt-design.md`

---

## File Structure

| File | Purpose | Created in task |
|---|---|---|
| `pyproject.toml` | Dependencies, pytest config | 1 |
| `.env.example` | Auth template | 1 |
| `app/__init__.py` | Package marker | 1 |
| `tests/__init__.py` | Package marker | 1 |
| `tests/conftest.py` | pytest-asyncio config | 1 |
| `app/agent.py` | LlmAgent definition | 2 |
| `tests/test_run_config.py` | VAD-disabled regression test | 3 |
| `app/server.py` | RUN_CONFIG, FastAPI app, /ws endpoint | 3, 11 |
| `app/bridge.py` | BridgeState, browser_to_adk, adk_to_browser | 4–10 |
| `tests/test_bridge.py` | All bridge unit tests | 4–10 |
| `app/static/index.html` | Single-page UI | 12 |
| `app/static/recorder-worklet.js` | AudioWorklet PCM converter | 13 |
| `app/static/player.js` | PCM playback queue | 14 |
| `app/static/ptt.js` | Spacebar handling, WS client, orchestration | 15 |
| `tests/SMOKE.md` | Manual smoke checklist | 16 |
| `README.md` | Setup & run instructions | 17 |
| `.github/workflows/test.yml` | CI: run pytest | 18 |

---

## Task 1: Project scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `.env.example`
- Create: `app/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "gemini-live-ptt"
version = "0.1.0"
description = "Push-to-talk demo for Gemini Live via Google ADK"
requires-python = ">=3.11"
dependencies = [
    "google-adk>=1.25,<2",
    "google-genai",
    "fastapi",
    "uvicorn[standard]",
    "python-dotenv",
]

[project.optional-dependencies]
dev = [
    "pytest>=7",
    "pytest-asyncio>=0.23",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 2: Create `.env.example`**

```
# Get an API key at https://aistudio.google.com/app/apikey
GOOGLE_API_KEY=your-key-here
```

- [ ] **Step 3: Create empty package markers**

Create `app/__init__.py` with content: (empty file)

Create `tests/__init__.py` with content: (empty file)

- [ ] **Step 4: Create `tests/conftest.py`**

```python
"""Shared pytest config. asyncio_mode=auto lives in pyproject.toml."""
```

- [ ] **Step 5: Set up venv and install**

Run:
```bash
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

Expected: installs `google-adk`, `google-genai`, `fastapi`, `uvicorn`, `pytest`, `pytest-asyncio` and dependencies. No errors.

- [ ] **Step 6: Verify pytest discovers no tests yet**

Run:
```bash
.venv/bin/pytest -q
```

Expected: `no tests ran in 0.0Xs` (or similar).

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml .env.example app/ tests/
git commit -m "chore: project scaffolding (pyproject, .env.example, app/ tests/ packages)"
```

---

## Task 2: Agent definition

**Files:**
- Create: `app/agent.py`

No automated test — `LlmAgent` is a data definition; correctness is exercised by smoke testing.

- [ ] **Step 1: Create `app/agent.py`**

```python
"""Gemini Live PTT agent.

The model constant is pinned here so swapping is a one-line change. Both
preview models are valid; we default to the newer 3.1 for lower latency.
Preview models can be deprecated with as little as 2 weeks' notice.
"""
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

- [ ] **Step 2: Verify it imports cleanly**

Run:
```bash
.venv/bin/python -c "from app.agent import root_agent; print(root_agent.name, root_agent.model)"
```

Expected output: `ptt_assistant gemini-3.1-flash-live-preview`

- [ ] **Step 3: Commit**

```bash
git add app/agent.py
git commit -m "feat: define LlmAgent for PTT (gemini-3.1-flash-live-preview)"
```

---

## Task 3: RUN_CONFIG with disabled VAD (TDD)

**Files:**
- Create: `tests/test_run_config.py`
- Create: `app/server.py` (initial — just RUN_CONFIG; FastAPI added in Task 11)

- [ ] **Step 1: Write the failing test**

Create `tests/test_run_config.py`:
```python
"""Regression guard for the core PTT switch: manual activity detection on."""
from app.server import RUN_CONFIG


def test_run_config_disables_vad():
    rid = RUN_CONFIG.realtime_input_config
    assert rid is not None
    aad = rid.automatic_activity_detection
    assert aad is not None
    assert aad.disabled is True


def test_run_config_response_modalities_audio():
    assert RUN_CONFIG.response_modalities == ["AUDIO"]


def test_run_config_streaming_mode_bidi():
    from google.adk.agents.run_config import StreamingMode
    assert RUN_CONFIG.streaming_mode == StreamingMode.BIDI


def test_run_config_has_input_and_output_transcription():
    assert RUN_CONFIG.input_audio_transcription is not None
    assert RUN_CONFIG.output_audio_transcription is not None
```

- [ ] **Step 2: Run the test and verify it fails**

Run:
```bash
.venv/bin/pytest tests/test_run_config.py -q
```

Expected: collection error or ImportError because `app.server` doesn't define `RUN_CONFIG` yet.

- [ ] **Step 3: Create `app/server.py` with just RUN_CONFIG**

```python
"""FastAPI server + ADK runner setup. Endpoint wiring added in Task 11."""
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.genai import types

RUN_CONFIG = RunConfig(
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

- [ ] **Step 4: Run the tests and verify they pass**

Run:
```bash
.venv/bin/pytest tests/test_run_config.py -q
```

Expected: `4 passed`.

- [ ] **Step 5: Commit**

```bash
git add tests/test_run_config.py app/server.py
git commit -m "feat: RUN_CONFIG with manual activity detection disabled"
```

---

## Task 4: Bridge — `BridgeState` + `browser_to_adk` handles `speech_start` (TDD)

**Files:**
- Create: `tests/test_bridge.py`
- Create: `app/bridge.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_bridge.py`:
```python
"""Unit tests for the WS <-> ADK bridge."""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.bridge import BridgeState, browser_to_adk


def make_ws(*messages):
    """Build a fake WS whose receive() yields the given messages then disconnects."""
    ws = AsyncMock()
    queue = list(messages) + [{"type": "websocket.disconnect", "code": 1000}]
    ws.receive.side_effect = queue
    return ws


def text_msg(payload: dict) -> dict:
    return {"type": "websocket.receive", "text": json.dumps(payload), "bytes": None}


def bytes_msg(b: bytes) -> dict:
    return {"type": "websocket.receive", "text": None, "bytes": b}


async def test_browser_to_adk_speech_start_calls_activity_start_and_clears_interrupting():
    ws = make_ws(text_msg({"type": "speech_start"}))
    live_queue = MagicMock()
    state = BridgeState(interrupting=True)

    await browser_to_adk(ws, live_queue, state)

    live_queue.send_activity_start.assert_called_once_with()
    assert state.interrupting is False
```

- [ ] **Step 2: Run the test and verify it fails**

Run:
```bash
.venv/bin/pytest tests/test_bridge.py -q
```

Expected: ImportError because `app.bridge` doesn't exist.

- [ ] **Step 3: Create minimal `app/bridge.py`**

```python
"""Browser ↔ ADK bridge.

Two coroutines:
- browser_to_adk: pulls WS messages, drives the LiveRequestQueue
- adk_to_browser: pulls live events, writes WS frames

Both share a BridgeState so barge-in semantics work cleanly.
"""
from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass
class BridgeState:
    interrupting: bool = False  # set by barge_in, cleared by next speech_start


async def browser_to_adk(ws, live_queue, state: BridgeState) -> None:
    """Pull messages from the websocket and call into the LiveRequestQueue."""
    while True:
        msg = await ws.receive()
        if msg["type"] == "websocket.disconnect":
            return
        text = msg.get("text")
        if text is not None:
            data = json.loads(text)
            kind = data.get("type")
            if kind == "speech_start":
                live_queue.send_activity_start()
                state.interrupting = False
```

- [ ] **Step 4: Run the test and verify it passes**

Run:
```bash
.venv/bin/pytest tests/test_bridge.py -q
```

Expected: `1 passed`.

- [ ] **Step 5: Commit**

```bash
git add app/bridge.py tests/test_bridge.py
git commit -m "feat(bridge): BridgeState + browser_to_adk handles speech_start"
```

---

## Task 5: Bridge — `browser_to_adk` handles audio frames (TDD)

**Files:**
- Modify: `tests/test_bridge.py`
- Modify: `app/bridge.py`

- [ ] **Step 1: Add the failing test**

Append to `tests/test_bridge.py`:
```python
async def test_browser_to_adk_audio_frame_calls_send_realtime_with_blob():
    pcm = b"\x00\x01" * 320  # ~20ms of fake 16-bit PCM
    ws = make_ws(bytes_msg(pcm))
    live_queue = MagicMock()
    state = BridgeState()

    await browser_to_adk(ws, live_queue, state)

    live_queue.send_realtime.assert_called_once()
    blob = live_queue.send_realtime.call_args.args[0]
    assert blob.data == pcm
    assert blob.mime_type == "audio/pcm;rate=16000"
```

- [ ] **Step 2: Run the test and verify it fails**

Run:
```bash
.venv/bin/pytest tests/test_bridge.py::test_browser_to_adk_audio_frame_calls_send_realtime_with_blob -q
```

Expected: FAIL — `send_realtime` not called.

- [ ] **Step 3: Update `app/bridge.py`**

Change the imports and `browser_to_adk` body:

```python
from __future__ import annotations

import json
from dataclasses import dataclass

from google.genai import types


@dataclass
class BridgeState:
    interrupting: bool = False


async def browser_to_adk(ws, live_queue, state: BridgeState) -> None:
    while True:
        msg = await ws.receive()
        if msg["type"] == "websocket.disconnect":
            return
        text = msg.get("text")
        audio = msg.get("bytes")
        if text is not None:
            data = json.loads(text)
            kind = data.get("type")
            if kind == "speech_start":
                live_queue.send_activity_start()
                state.interrupting = False
        elif audio is not None:
            live_queue.send_realtime(
                types.Blob(data=audio, mime_type="audio/pcm;rate=16000")
            )
```

- [ ] **Step 4: Run all bridge tests**

Run:
```bash
.venv/bin/pytest tests/test_bridge.py -q
```

Expected: `2 passed`.

- [ ] **Step 5: Commit**

```bash
git add app/bridge.py tests/test_bridge.py
git commit -m "feat(bridge): browser_to_adk forwards PCM frames as realtime blobs"
```

---

## Task 6: Bridge — `browser_to_adk` handles `speech_end` (TDD)

**Files:**
- Modify: `tests/test_bridge.py`
- Modify: `app/bridge.py`

- [ ] **Step 1: Add the failing test**

Append to `tests/test_bridge.py`:
```python
async def test_browser_to_adk_speech_end_calls_activity_end():
    ws = make_ws(text_msg({"type": "speech_end"}))
    live_queue = MagicMock()
    state = BridgeState()

    await browser_to_adk(ws, live_queue, state)

    live_queue.send_activity_end.assert_called_once_with()
```

- [ ] **Step 2: Run the test and verify it fails**

Run:
```bash
.venv/bin/pytest tests/test_bridge.py::test_browser_to_adk_speech_end_calls_activity_end -q
```

Expected: FAIL.

- [ ] **Step 3: Update `browser_to_adk` in `app/bridge.py`**

Add an `elif` branch for `speech_end`:

```python
            if kind == "speech_start":
                live_queue.send_activity_start()
                state.interrupting = False
            elif kind == "speech_end":
                live_queue.send_activity_end()
```

- [ ] **Step 4: Run all bridge tests**

Run:
```bash
.venv/bin/pytest tests/test_bridge.py -q
```

Expected: `3 passed`.

- [ ] **Step 5: Commit**

```bash
git add app/bridge.py tests/test_bridge.py
git commit -m "feat(bridge): browser_to_adk forwards speech_end as activity_end"
```

---

## Task 7: Bridge — `browser_to_adk` handles `barge_in` (TDD)

**Files:**
- Modify: `tests/test_bridge.py`
- Modify: `app/bridge.py`

- [ ] **Step 1: Add the failing test**

Append to `tests/test_bridge.py`:
```python
async def test_browser_to_adk_barge_in_sets_interrupting_flag():
    ws = make_ws(text_msg({"type": "barge_in"}))
    live_queue = MagicMock()
    state = BridgeState(interrupting=False)

    await browser_to_adk(ws, live_queue, state)

    assert state.interrupting is True
    # barge_in alone does not call ADK; the subsequent speech_start does
    live_queue.send_activity_start.assert_not_called()
    live_queue.send_activity_end.assert_not_called()


async def test_browser_to_adk_barge_in_then_speech_start_clears_interrupting():
    ws = make_ws(
        text_msg({"type": "barge_in"}),
        text_msg({"type": "speech_start"}),
    )
    live_queue = MagicMock()
    state = BridgeState(interrupting=False)

    await browser_to_adk(ws, live_queue, state)

    assert state.interrupting is False
    live_queue.send_activity_start.assert_called_once_with()
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:
```bash
.venv/bin/pytest tests/test_bridge.py -q -k barge_in
```

Expected: 2 failures.

- [ ] **Step 3: Update `browser_to_adk` in `app/bridge.py`**

Add the `barge_in` branch:

```python
            if kind == "speech_start":
                live_queue.send_activity_start()
                state.interrupting = False
            elif kind == "speech_end":
                live_queue.send_activity_end()
            elif kind == "barge_in":
                state.interrupting = True
```

- [ ] **Step 4: Run all bridge tests**

Run:
```bash
.venv/bin/pytest tests/test_bridge.py -q
```

Expected: `5 passed`.

- [ ] **Step 5: Commit**

```bash
git add app/bridge.py tests/test_bridge.py
git commit -m "feat(bridge): browser_to_adk sets interrupting flag on barge_in"
```

---

## Task 8: Bridge — `adk_to_browser` forwards audio as binary (TDD)

**Files:**
- Modify: `tests/test_bridge.py`
- Modify: `app/bridge.py`

The model emits audio inside `event.content.parts[*].inline_data` (a `types.Blob`). The bridge needs a small helper to extract those bytes.

- [ ] **Step 1: Add the failing test**

Append to `tests/test_bridge.py`:
```python
from app.bridge import adk_to_browser


def fake_event(*, audio: bytes | None = None,
               input_text: str | None = None, input_final: bool = False,
               output_text: str | None = None, output_final: bool = False,
               turn_complete: bool = False, interrupted: bool = False):
    """Build a MagicMock that mirrors the LlmResponse/Event shape we read."""
    ev = MagicMock()
    if audio is not None:
        part = MagicMock()
        part.inline_data = MagicMock(data=audio, mime_type="audio/pcm;rate=24000")
        ev.content = MagicMock(parts=[part])
    else:
        ev.content = None
    if input_text is not None:
        ev.input_transcription = MagicMock(text=input_text, finished=input_final)
    else:
        ev.input_transcription = None
    if output_text is not None:
        ev.output_transcription = MagicMock(text=output_text, finished=output_final)
    else:
        ev.output_transcription = None
    ev.turn_complete = turn_complete
    ev.interrupted = interrupted
    return ev


async def fake_events(*evs):
    for e in evs:
        yield e


async def test_adk_to_browser_forwards_audio_as_binary():
    pcm = b"\x10\x20" * 50
    ws = AsyncMock()
    state = BridgeState()

    await adk_to_browser(ws, fake_events(fake_event(audio=pcm)), state)

    ws.send_bytes.assert_called_once_with(pcm)
    ws.send_text.assert_not_called()
```

- [ ] **Step 2: Run the test and verify it fails**

Run:
```bash
.venv/bin/pytest tests/test_bridge.py::test_adk_to_browser_forwards_audio_as_binary -q
```

Expected: ImportError (`adk_to_browser` not defined).

- [ ] **Step 3: Add `adk_to_browser` to `app/bridge.py`**

Append to `app/bridge.py`:

```python
def _extract_audio(event) -> bytes | None:
    """Return raw PCM bytes from an event's first inline_data part, if any."""
    content = getattr(event, "content", None)
    parts = getattr(content, "parts", None) if content is not None else None
    if not parts:
        return None
    for part in parts:
        inline = getattr(part, "inline_data", None)
        data = getattr(inline, "data", None) if inline is not None else None
        if data:
            return data
    return None


async def adk_to_browser(ws, live_events, state: BridgeState) -> None:
    """Pull events from ADK and write them to the websocket."""
    async for event in live_events:
        audio = _extract_audio(event)
        if audio is not None:
            await ws.send_bytes(audio)
```

- [ ] **Step 4: Run the test and verify it passes**

Run:
```bash
.venv/bin/pytest tests/test_bridge.py -q
```

Expected: `6 passed`.

- [ ] **Step 5: Commit**

```bash
git add app/bridge.py tests/test_bridge.py
git commit -m "feat(bridge): adk_to_browser forwards model audio as binary frames"
```

---

## Task 9: Bridge — `adk_to_browser` forwards transcripts and turn_complete (TDD)

**Files:**
- Modify: `tests/test_bridge.py`
- Modify: `app/bridge.py`

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_bridge.py`:
```python
async def test_adk_to_browser_forwards_input_transcript_as_json():
    ws = AsyncMock()
    state = BridgeState()
    ev = fake_event(input_text="hello there", input_final=True)

    await adk_to_browser(ws, fake_events(ev), state)

    ws.send_text.assert_called_once()
    payload = json.loads(ws.send_text.call_args.args[0])
    assert payload == {"type": "input_transcript", "text": "hello there", "final": True}


async def test_adk_to_browser_forwards_output_transcript_as_json():
    ws = AsyncMock()
    state = BridgeState()
    ev = fake_event(output_text="hi back", output_final=False)

    await adk_to_browser(ws, fake_events(ev), state)

    payload = json.loads(ws.send_text.call_args.args[0])
    assert payload == {"type": "output_transcript", "text": "hi back", "final": False}


async def test_adk_to_browser_forwards_turn_complete():
    ws = AsyncMock()
    state = BridgeState()

    await adk_to_browser(ws, fake_events(fake_event(turn_complete=True)), state)

    payload = json.loads(ws.send_text.call_args.args[0])
    assert payload == {"type": "turn_complete"}


async def test_adk_to_browser_forwards_interrupted():
    ws = AsyncMock()
    state = BridgeState()

    await adk_to_browser(ws, fake_events(fake_event(interrupted=True)), state)

    payloads = [json.loads(c.args[0]) for c in ws.send_text.call_args_list]
    assert {"type": "interrupted"} in payloads
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:
```bash
.venv/bin/pytest tests/test_bridge.py -q -k "transcript or turn_complete or interrupted"
```

Expected: 4 failures.

- [ ] **Step 3: Extend `adk_to_browser` in `app/bridge.py`**

Replace the `adk_to_browser` body with:

```python
async def adk_to_browser(ws, live_events, state: BridgeState) -> None:
    async for event in live_events:
        audio = _extract_audio(event)
        if audio is not None:
            await ws.send_bytes(audio)

        in_t = getattr(event, "input_transcription", None)
        if in_t is not None and getattr(in_t, "text", None):
            await ws.send_text(json.dumps({
                "type": "input_transcript",
                "text": in_t.text,
                "final": bool(getattr(in_t, "finished", False)),
            }))

        out_t = getattr(event, "output_transcription", None)
        if out_t is not None and getattr(out_t, "text", None):
            await ws.send_text(json.dumps({
                "type": "output_transcript",
                "text": out_t.text,
                "final": bool(getattr(out_t, "finished", False)),
            }))

        if getattr(event, "interrupted", False):
            await ws.send_text(json.dumps({"type": "interrupted"}))

        if getattr(event, "turn_complete", False):
            await ws.send_text(json.dumps({"type": "turn_complete"}))
```

- [ ] **Step 4: Run all bridge tests**

Run:
```bash
.venv/bin/pytest tests/test_bridge.py -q
```

Expected: `10 passed`.

- [ ] **Step 5: Commit**

```bash
git add app/bridge.py tests/test_bridge.py
git commit -m "feat(bridge): forward transcripts, turn_complete, interrupted"
```

---

## Task 10: Bridge — `adk_to_browser` drops in-flight audio/transcripts when `state.interrupting` (TDD)

**Files:**
- Modify: `tests/test_bridge.py`
- Modify: `app/bridge.py`

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_bridge.py`:
```python
async def test_adk_to_browser_drops_audio_while_interrupting():
    pcm = b"\xff" * 16
    ws = AsyncMock()
    state = BridgeState(interrupting=True)

    await adk_to_browser(ws, fake_events(fake_event(audio=pcm)), state)

    ws.send_bytes.assert_not_called()


async def test_adk_to_browser_drops_transcript_while_interrupting():
    ws = AsyncMock()
    state = BridgeState(interrupting=True)
    ev = fake_event(output_text="stale words", output_final=True)

    await adk_to_browser(ws, fake_events(ev), state)

    ws.send_text.assert_not_called()


async def test_adk_to_browser_still_forwards_turn_complete_while_interrupting():
    """turn_complete is a control signal; UI may want to know the old turn ended.
    Drop logic must not suppress everything. If you change this behavior, update
    the test."""
    ws = AsyncMock()
    state = BridgeState(interrupting=True)

    await adk_to_browser(ws, fake_events(fake_event(turn_complete=True)), state)

    payloads = [json.loads(c.args[0]) for c in ws.send_text.call_args_list]
    assert {"type": "turn_complete"} in payloads
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:
```bash
.venv/bin/pytest tests/test_bridge.py -q -k interrupting
```

Expected: 2 failures (`drops_audio` and `drops_transcript`); the `turn_complete` test happens to already pass — keep it as a regression guard.

- [ ] **Step 3: Update `adk_to_browser` to honor `state.interrupting`**

Replace `adk_to_browser` body:

```python
async def adk_to_browser(ws, live_events, state: BridgeState) -> None:
    async for event in live_events:
        # Audio + transcript content from a turn that was interrupted is stale.
        # Drop it so the UI doesn't replay/display it after the user barged in.
        if not state.interrupting:
            audio = _extract_audio(event)
            if audio is not None:
                await ws.send_bytes(audio)

            in_t = getattr(event, "input_transcription", None)
            if in_t is not None and getattr(in_t, "text", None):
                await ws.send_text(json.dumps({
                    "type": "input_transcript",
                    "text": in_t.text,
                    "final": bool(getattr(in_t, "finished", False)),
                }))

            out_t = getattr(event, "output_transcription", None)
            if out_t is not None and getattr(out_t, "text", None):
                await ws.send_text(json.dumps({
                    "type": "output_transcript",
                    "text": out_t.text,
                    "final": bool(getattr(out_t, "finished", False)),
                }))

        if getattr(event, "interrupted", False):
            await ws.send_text(json.dumps({"type": "interrupted"}))

        if getattr(event, "turn_complete", False):
            await ws.send_text(json.dumps({"type": "turn_complete"}))
```

- [ ] **Step 4: Run all bridge tests**

Run:
```bash
.venv/bin/pytest tests/test_bridge.py -q
```

Expected: `13 passed`.

- [ ] **Step 5: Commit**

```bash
git add app/bridge.py tests/test_bridge.py
git commit -m "feat(bridge): drop in-flight audio/transcripts during barge-in"
```

---

## Task 11: Server — FastAPI app, `/`, `/ws` endpoint

**Files:**
- Modify: `app/server.py`

No automated test — the WS handler is integration code. Verified end-to-end via smoke test.

- [ ] **Step 1: Replace `app/server.py` with the full implementation**

```python
"""FastAPI server: static UI + /ws bridge for Gemini Live PTT."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from google.adk.agents.live_request_queue import LiveRequestQueue
from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.runners import InMemoryRunner
from google.genai import types

from app.agent import root_agent
from app.bridge import BridgeState, adk_to_browser, browser_to_adk

load_dotenv()  # picks up GOOGLE_API_KEY from .env

logger = logging.getLogger("ptt")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

APP_NAME = "ptt"
USER_ID = "local"

RUN_CONFIG = RunConfig(
    streaming_mode=StreamingMode.BIDI,
    response_modalities=["AUDIO"],
    realtime_input_config=types.RealtimeInputConfig(
        automatic_activity_detection=types.AutomaticActivityDetection(
            disabled=True,
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

# One runner per process; sessions are per-WS-connection.
runner = InMemoryRunner(agent=root_agent, app_name=APP_NAME)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("ptt server starting; model=%s", root_agent.model)
    yield
    logger.info("ptt server stopping")


app = FastAPI(lifespan=lifespan)

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    logger.info("ws connected")
    session = await runner.session_service.create_session(
        app_name=APP_NAME, user_id=USER_ID
    )
    live_queue = LiveRequestQueue()
    live_events = runner.run_live(
        user_id=USER_ID,
        session_id=session.id,
        live_request_queue=live_queue,
        run_config=RUN_CONFIG,
    )
    state = BridgeState()

    try:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(browser_to_adk(ws, live_queue, state))
            tg.create_task(adk_to_browser(ws, live_events, state))
    except* Exception as eg:  # noqa: F841
        for exc in eg.exceptions:
            logger.warning("bridge task error: %r", exc)
    finally:
        live_queue.close()
        try:
            await runner.session_service.delete_session(
                app_name=APP_NAME, user_id=USER_ID, session_id=session.id
            )
        except Exception as e:
            logger.warning("session delete failed: %r", e)
        logger.info("ws closed")
```

- [ ] **Step 2: Verify imports & app construction**

Run:
```bash
.venv/bin/python -c "from app.server import app, RUN_CONFIG, runner; print('ok', RUN_CONFIG.streaming_mode)"
```

Expected: `ok StreamingMode.BIDI`

- [ ] **Step 3: Re-run the run_config tests (sanity)**

Run:
```bash
.venv/bin/pytest tests/test_run_config.py -q
```

Expected: `4 passed`.

- [ ] **Step 4: Commit**

```bash
git add app/server.py
git commit -m "feat(server): FastAPI app with /ws endpoint wiring ADK to bridge"
```

---

## Task 12: Frontend — `index.html`

**Files:**
- Create: `app/static/index.html`

No automated test — verified via smoke.

- [ ] **Step 1: Create `app/static/index.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Gemini Live PTT</title>
  <style>
    :root { color-scheme: dark; }
    body { font-family: ui-sans-serif, system-ui, sans-serif; max-width: 640px;
           margin: 2rem auto; padding: 0 1rem; background: #111; color: #eee; }
    header { display: flex; align-items: center; gap: 0.75rem; margin-bottom: 1rem; }
    h1 { font-size: 1.1rem; margin: 0; font-weight: 600; }
    #status { font-size: 0.8rem; padding: 0.15rem 0.6rem; border-radius: 999px;
              background: #333; color: #ccc; }
    #status.ready { background: #064; color: #afa; }
    #status.recording { background: #804; color: #fbb; }
    #status.error { background: #800; color: #fdd; }
    #hint { color: #888; font-size: 0.85rem; margin-bottom: 1rem; }
    #transcript { background: #1b1b1b; border: 1px solid #2b2b2b;
                  padding: 0.75rem 1rem; border-radius: 6px; min-height: 16rem; }
    .turn { margin-bottom: 0.75rem; }
    .turn .label { font-size: 0.7rem; color: #888; text-transform: uppercase;
                   letter-spacing: 0.05em; margin-bottom: 0.15rem; }
    .turn.user .label { color: #6af; }
    .turn.model .label { color: #fa6; }
    #overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.85);
               display: none; align-items: center; justify-content: center;
               text-align: center; padding: 2rem; }
    #overlay.show { display: flex; }
  </style>
</head>
<body>
  <header>
    <h1>Gemini Live — Push to Talk</h1>
    <span id="status">connecting…</span>
  </header>
  <div id="hint">Hold <kbd>SPACE</kbd> to talk. Release to send. Press again mid-response to interrupt.</div>
  <div id="transcript" aria-live="polite"></div>
  <div id="overlay">
    <div>
      <h2>Microphone permission required</h2>
      <p>Grant mic access in the browser address bar, then reload.</p>
    </div>
  </div>
  <script type="module" src="/static/ptt.js"></script>
</body>
</html>
```

- [ ] **Step 2: Verify the page loads**

Run:
```bash
.venv/bin/uvicorn app.server:app --port 8000 &
sleep 1
curl -s http://localhost:8000/ | head -5
kill %1
```

Expected: HTML output starting with `<!DOCTYPE html>`.

- [ ] **Step 3: Commit**

```bash
git add app/static/index.html
git commit -m "feat(ui): index.html shell with status pill and transcript pane"
```

---

## Task 13: Frontend — `recorder-worklet.js` (AudioWorklet that emits 16 kHz 16-bit PCM)

**Files:**
- Create: `app/static/recorder-worklet.js`

- [ ] **Step 1: Create `app/static/recorder-worklet.js`**

```javascript
// AudioWorklet processor: takes float32 mono audio at the AudioContext's
// native rate, downsamples to 16 kHz, converts to 16-bit little-endian PCM,
// and posts ArrayBuffers of ~20ms (640 bytes = 320 samples) to the main thread.
class RecorderProcessor extends AudioWorkletProcessor {
  constructor(opts) {
    super();
    const { sourceRate } = opts.processorOptions;
    this.sourceRate = sourceRate;
    this.targetRate = 16000;
    this.ratio = sourceRate / this.targetRate; // e.g. 48000/16000 = 3
    this.frameSamples = 320; // 20 ms @ 16 kHz
    this.outBuf = new Int16Array(this.frameSamples);
    this.outIdx = 0;
    this.acc = 0;
    this.accCount = 0;
    this.sampleSinceLast = 0;
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || input.length === 0) return true;
    const channel = input[0];
    if (!channel) return true;

    for (let i = 0; i < channel.length; i++) {
      this.acc += channel[i];
      this.accCount += 1;
      this.sampleSinceLast += 1;
      if (this.sampleSinceLast >= this.ratio) {
        const avg = this.acc / this.accCount;
        const s = Math.max(-1, Math.min(1, avg));
        this.outBuf[this.outIdx++] = s < 0 ? s * 0x8000 : s * 0x7fff;
        this.acc = 0;
        this.accCount = 0;
        this.sampleSinceLast -= this.ratio;
        if (this.outIdx >= this.frameSamples) {
          this.port.postMessage(this.outBuf.slice().buffer);
          this.outIdx = 0;
        }
      }
    }
    return true;
  }
}

registerProcessor("recorder-processor", RecorderProcessor);
```

- [ ] **Step 2: Commit (no test — verified via smoke)**

```bash
git add app/static/recorder-worklet.js
git commit -m "feat(ui): AudioWorklet recorder downsamples to 16 kHz 16-bit PCM"
```

---

## Task 14: Frontend — `player.js` (PCM 24 kHz playback queue)

**Files:**
- Create: `app/static/player.js`

- [ ] **Step 1: Create `app/static/player.js`**

```javascript
// Plays incoming 16-bit PCM frames at 24 kHz mono via chained AudioBufferSourceNodes.
// flush() stops everything immediately for barge-in.
export class Player {
  constructor(ctx) {
    this.ctx = ctx;
    this.rate = 24000;
    this.nextStart = 0;       // AudioContext time when the next buffer should play
    this.active = new Set();  // currently scheduled BufferSourceNodes
  }

  enqueue(arrayBuffer) {
    const i16 = new Int16Array(arrayBuffer);
    const f32 = new Float32Array(i16.length);
    for (let i = 0; i < i16.length; i++) f32[i] = i16[i] / 0x8000;

    const buf = this.ctx.createBuffer(1, f32.length, this.rate);
    buf.getChannelData(0).set(f32);

    const src = this.ctx.createBufferSource();
    src.buffer = buf;
    src.connect(this.ctx.destination);

    const now = this.ctx.currentTime;
    const startAt = Math.max(now + 0.1, this.nextStart);  // 100 ms initial jitter buffer
    src.start(startAt);
    this.nextStart = startAt + buf.duration;

    this.active.add(src);
    src.onended = () => this.active.delete(src);
  }

  flush() {
    for (const src of this.active) {
      try { src.stop(); } catch (_) { /* already stopped */ }
    }
    this.active.clear();
    this.nextStart = this.ctx.currentTime;
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add app/static/player.js
git commit -m "feat(ui): PCM 24 kHz player with chained source nodes + flush()"
```

---

## Task 15: Frontend — `ptt.js` (spacebar handling, WS client, orchestration)

**Files:**
- Create: `app/static/ptt.js`

Note: this file constructs DOM nodes via `createElement` + `textContent` rather than `innerHTML`, since the transcript text comes from the model and could in principle contain markup-like characters.

- [ ] **Step 1: Create `app/static/ptt.js`**

```javascript
import { Player } from "/static/player.js";

const SPEECH_END_GRACE_MS = 200;    // keep streaming briefly after key release
const MAX_TURN_MS = 60_000;          // hard cap per press
const RECONNECT_BACKOFF = [250, 500, 1000, 2000, 5000];

const statusEl = document.getElementById("status");
const transcriptEl = document.getElementById("transcript");
const overlayEl = document.getElementById("overlay");

function setStatus(text, cls = "") {
  statusEl.textContent = text;
  statusEl.className = cls;
}

// ---- Audio capture ------------------------------------------------------

let audioCtx, micStream, workletNode, player;
let isPressed = false;
let isModelSpeaking = false;
let endGraceTimer = null;
let maxTurnTimer = null;

async function initAudio() {
  try {
    micStream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 },
    });
  } catch (err) {
    console.error("getUserMedia failed", err);
    overlayEl.classList.add("show");
    throw err;
  }
  audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  await audioCtx.audioWorklet.addModule("/static/recorder-worklet.js");
  const src = audioCtx.createMediaStreamSource(micStream);
  workletNode = new AudioWorkletNode(audioCtx, "recorder-processor", {
    processorOptions: { sourceRate: audioCtx.sampleRate },
  });
  src.connect(workletNode);
  // Don't connect worklet to destination — we don't want to hear ourselves.
  workletNode.port.onmessage = (e) => {
    if (!isPressed && endGraceTimer === null) return;  // gate is closed
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(e.data);
    }
  };
  player = new Player(audioCtx);
}

// ---- WebSocket ----------------------------------------------------------

let ws = null;
let backoffIdx = 0;

function connect() {
  setStatus("connecting…");
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.binaryType = "arraybuffer";

  ws.onopen = () => {
    backoffIdx = 0;
    setStatus("ready", "ready");
  };

  ws.onmessage = (e) => {
    if (typeof e.data === "string") {
      handleControl(JSON.parse(e.data));
    } else {
      isModelSpeaking = true;
      player.enqueue(e.data);
    }
  };

  ws.onclose = () => {
    setStatus("reconnecting…");
    isPressed = false;
    if (endGraceTimer) { clearTimeout(endGraceTimer); endGraceTimer = null; }
    const delay = RECONNECT_BACKOFF[Math.min(backoffIdx++, RECONNECT_BACKOFF.length - 1)];
    setTimeout(connect, delay);
  };

  ws.onerror = () => setStatus("error", "error");
}

// ---- Transcript rendering ----------------------------------------------

let currentUserTurn = null;
let currentModelTurn = null;

function appendTurn(role) {
  const turnEl = document.createElement("div");
  turnEl.className = `turn ${role}`;
  const labelEl = document.createElement("div");
  labelEl.className = "label";
  labelEl.textContent = role === "user" ? "You" : "Gemini";
  const textEl = document.createElement("div");
  textEl.className = "text";
  turnEl.appendChild(labelEl);
  turnEl.appendChild(textEl);
  transcriptEl.appendChild(turnEl);
  transcriptEl.scrollTop = transcriptEl.scrollHeight;
  return textEl;
}

function handleControl(msg) {
  switch (msg.type) {
    case "input_transcript": {
      if (!currentUserTurn) currentUserTurn = appendTurn("user");
      currentUserTurn.textContent = msg.text;
      if (msg.final) currentUserTurn = null;
      break;
    }
    case "output_transcript": {
      if (!currentModelTurn) currentModelTurn = appendTurn("model");
      currentModelTurn.textContent = (currentModelTurn.textContent || "") + msg.text;
      if (msg.final) currentModelTurn = null;
      break;
    }
    case "turn_complete":
      isModelSpeaking = false;
      currentUserTurn = null;
      currentModelTurn = null;
      setStatus("ready", "ready");
      break;
    case "interrupted":
      // confirmation only — UI already flushed locally
      break;
    case "error":
      setStatus(`error: ${msg.message}`, "error");
      break;
  }
}

// ---- Spacebar handling --------------------------------------------------

function pressStart() {
  if (isPressed) return;
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  isPressed = true;
  if (endGraceTimer) { clearTimeout(endGraceTimer); endGraceTimer = null; }

  if (isModelSpeaking) {
    player.flush();
    ws.send(JSON.stringify({ type: "barge_in" }));
    isModelSpeaking = false;
    currentModelTurn = null;
  }
  ws.send(JSON.stringify({ type: "speech_start" }));
  setStatus("recording…", "recording");

  maxTurnTimer = setTimeout(() => {
    if (isPressed) {
      setStatus("max turn length reached", "error");
      pressEnd();
    }
  }, MAX_TURN_MS);
}

function pressEnd() {
  if (!isPressed) return;
  isPressed = false;
  if (maxTurnTimer) { clearTimeout(maxTurnTimer); maxTurnTimer = null; }

  // Keep the gate open during the grace period so the worklet's last
  // ~200ms of audio still gets sent before we signal speech_end.
  endGraceTimer = setTimeout(() => {
    endGraceTimer = null;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "speech_end" }));
    }
    setStatus("ready", "ready");
  }, SPEECH_END_GRACE_MS);
}

window.addEventListener("keydown", (e) => {
  if (e.code === "Space" && !e.repeat && !e.target.matches("input,textarea")) {
    e.preventDefault();
    pressStart();
  }
});
window.addEventListener("keyup", (e) => {
  if (e.code === "Space") {
    e.preventDefault();
    pressEnd();
  }
});
document.addEventListener("visibilitychange", () => {
  if (document.hidden && isPressed) pressEnd();
});

// ---- Boot ---------------------------------------------------------------

initAudio().then(connect).catch((err) => {
  console.error("init failed", err);
  setStatus("init failed", "error");
});
```

- [ ] **Step 2: Confirm the file is loadable as a module**

Run:
```bash
.venv/bin/uvicorn app.server:app --port 8000 &
sleep 1
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/static/ptt.js
kill %1
```

Expected: `200`.

- [ ] **Step 3: Commit**

```bash
git add app/static/ptt.js
git commit -m "feat(ui): PTT spacebar handler, WS client, transcript rendering"
```

---

## Task 16: Smoke test checklist

**Files:**
- Create: `tests/SMOKE.md`

- [ ] **Step 1: Create `tests/SMOKE.md`**

````markdown
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
````

- [ ] **Step 2: Commit**

```bash
git add tests/SMOKE.md
git commit -m "docs: manual smoke test checklist"
```

---

## Task 17: README

**Files:**
- Create: `README.md`

- [ ] **Step 1: Create `README.md`**

````markdown
# gemini-live-ptt

Single-user push-to-talk web app for Gemini Live, built on Google ADK.
Disables Gemini Live's automatic VAD so the spacebar is the sole turn-boundary signal.

## Quickstart

```bash
python3.12 -m venv .venv
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
````

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: README with quickstart and pointers to spec & plan"
```

---

## Task 18: CI — GitHub Actions pytest workflow

**Files:**
- Create: `.github/workflows/test.yml`

- [ ] **Step 1: Create `.github/workflows/test.yml`**

```yaml
name: test

on:
  push:
    branches: [main]
  pull_request:

jobs:
  pytest:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      - name: Install
        run: pip install -e '.[dev]'
      - name: Run tests
        run: pytest -q
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/test.yml
git commit -m "ci: run pytest on push and PR"
```

---

## Final verification

- [ ] **Step 1: Full test run**

```bash
.venv/bin/pytest -q
```

Expected: `17 passed` (4 run_config + 13 bridge).

- [ ] **Step 2: Server boots cleanly**

```bash
.venv/bin/uvicorn app.server:app --port 8000 &
sleep 2
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/
kill %1
```

Expected: `200`.

- [ ] **Step 3: Run the manual smoke checklist (`tests/SMOKE.md`)**

Walk through all 7 items. Stop and fix anything that doesn't pass.

- [ ] **Step 4: Confirm spec open questions are resolved**

Two open questions from the spec:

1. **Exact ADK event field names for input/output transcription.** Resolved by inspecting `LlmResponse`: fields are `input_transcription` and `output_transcription`, each a `types.Transcription` with `.text` and `.finished`. Implemented in Task 9.

2. **Whether the browser-side flush + server-side `interrupting` flag are both needed.** During smoke test 3, observe: does any stale audio reach the browser between pressing spacebar and the new turn starting? The browser-side `player.flush()` is needed regardless (UX: silence within 50 ms), so the question is really whether the server-side drop is also needed. If smoke test shows zero stale audio reaches the browser even without the server-side flag, the bridge logic could be simplified. Otherwise keep both.
