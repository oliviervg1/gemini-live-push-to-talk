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


async def test_browser_to_adk_speech_end_calls_activity_end():
    ws = make_ws(text_msg({"type": "speech_end"}))
    live_queue = MagicMock()
    state = BridgeState()

    await browser_to_adk(ws, live_queue, state)

    live_queue.send_activity_end.assert_called_once_with()


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
