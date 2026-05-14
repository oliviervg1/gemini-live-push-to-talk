"""Unit tests for the WS <-> ADK bridge."""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.bridge import BridgeState, adk_to_browser, browser_to_adk


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

    await adk_to_browser(ws, fake_events(*[fake_event(audio=pcm)]), state)

    ws.send_bytes.assert_called_once_with(pcm)
    ws.send_text.assert_not_called()


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
