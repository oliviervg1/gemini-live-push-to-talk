"""Browser ↔ ADK bridge.

Two coroutines:
- browser_to_adk: pulls WS messages, drives the LiveRequestQueue
- adk_to_browser: pulls live events, writes WS frames

Both share a BridgeState so barge-in semantics work cleanly.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from google.genai import types


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
        audio = msg.get("bytes")
        if text is not None:
            data = json.loads(text)
            kind = data.get("type")
            if kind == "speech_start":
                live_queue.send_activity_start()
                state.interrupting = False
            elif kind == "speech_end":
                live_queue.send_activity_end()
            elif kind == "barge_in":
                state.interrupting = True
        elif audio is not None:
            live_queue.send_realtime(
                types.Blob(data=audio, mime_type="audio/pcm;rate=16000")
            )


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
