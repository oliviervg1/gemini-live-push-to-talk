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
