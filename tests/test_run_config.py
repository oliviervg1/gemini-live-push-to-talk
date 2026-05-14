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
