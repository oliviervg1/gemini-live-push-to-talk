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
