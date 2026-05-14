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
        elif audio is not None:
            live_queue.send_realtime(
                types.Blob(data=audio, mime_type="audio/pcm;rate=16000")
            )
