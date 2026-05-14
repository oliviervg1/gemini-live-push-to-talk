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
