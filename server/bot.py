"""Buddy — Voice Companion Server (Local-First).

Uses whisper.cpp for STT, Kokoro for TTS, and Claude for the LLM.
Only paid service is Anthropic Claude for the LLM.

Run with: uv run bot.py
Then open http://localhost:7860/client in your browser.
"""

import os
import sys

# Ensure server directory is in path for local imports
sys.path.insert(0, os.path.dirname(__file__))

from loguru import logger

print("🐾 Starting Buddy voice companion (local-first)...")
print("⏳ Loading models...\n")

logger.info("Loading Silero VAD model...")
from pipecat.audio.vad.silero import SileroVADAnalyzer

logger.info("Silero VAD loaded")

import time

from pipecat.frames.frames import (
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMRunFrame,
    TranscriptionFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.anthropic.llm import AnthropicLLMService
from pipecat.transports.base_transport import BaseTransport, TransportParams

# Local STT + TTS
from stt_whisper import WhisperSTTProcessor
from pipecat.services.kokoro.tts import KokoroTTSService

logger.info("All components loaded")

import config  # Validates config on import


class PipelineLogger(FrameProcessor):
    """Logs key frames as they flow through the pipeline for observability."""

    def __init__(self, name: str):
        super().__init__()
        self._name = name
        self._llm_start: float = 0
        self._tts_start: float = 0

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptionFrame):
            logger.info(f"[{self._name}] transcription -> LLM: \"{frame.text}\"")
            self._llm_start = time.monotonic()

        elif isinstance(frame, LLMFullResponseStartFrame):
            ttfb = (time.monotonic() - self._llm_start) * 1000 if self._llm_start else 0
            logger.info(f"[{self._name}] LLM first token: {ttfb:.0f}ms")

        elif isinstance(frame, LLMFullResponseEndFrame):
            elapsed = (time.monotonic() - self._llm_start) * 1000 if self._llm_start else 0
            logger.info(f"[{self._name}] LLM done: {elapsed:.0f}ms total")

        elif isinstance(frame, TTSStartedFrame):
            self._tts_start = time.monotonic()
            logger.info(f"[{self._name}] TTS started")

        elif isinstance(frame, TTSStoppedFrame):
            elapsed = (time.monotonic() - self._tts_start) * 1000 if self._tts_start else 0
            logger.info(f"[{self._name}] TTS done: {elapsed:.0f}ms")

        await self.push_frame(frame, direction)

# ── System Prompt ──────────────────────────────────────────────
SYSTEM_PROMPT = """You are Buddy, a warm and witty voice companion. You speak out loud to Elie.

Voice rules (CRITICAL — you are being spoken aloud via TTS):
- Keep responses to 1-3 sentences unless the user asks for detail.
- Never use markdown, bullet points, asterisks, code blocks, or any formatting.
- Never use emojis or special characters.
- Use natural conversational language. Say "about twenty bucks" not "$19.99".
- Use contractions: "I'm", "you're", "that's", "don't".
- Don't start with "I'd be happy to help" or "Great question" — just answer.
- You can say "hmm", "yeah", "well", "so", "anyway" — be human.
- If you don't know something, say so briefly. Don't ramble.
- Match the user's energy: casual question gets casual answer, serious gets serious.

About Elie:
- Engineer and product person, sharp, values directness.
- Timezone: US Pacific (PST/PDT).
- Prefers no fluff — get to the point.

You are NOT a generic assistant. You're Buddy — a companion. Be yourself."""


async def run_bot(transport: BaseTransport, runner_args: RunnerArguments):
    """Configure and run the voice pipeline."""
    logger.info("Configuring pipeline...")

    # ── Services ───────────────────────────────────────────────
    stt = WhisperSTTProcessor(
        server_url=config.WHISPER_SERVER_URL,
    )

    llm = AnthropicLLMService(
        api_key=config.ANTHROPIC_API_KEY,
        model=config.LLM_MODEL,
    )

    tts = KokoroTTSService(
        voice_id=config.KOKORO_VOICE,
    )

    # ── Conversation Context ───────────────────────────────────
    context = LLMContext(
        messages=[{"role": "system", "content": SYSTEM_PROMPT}],
    )

    context_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(),
        ),
    )

    # ── Pipeline ───────────────────────────────────────────────
    # Data flows left-to-right:
    #   audio in → STT → context → LLM → TTS → [log] → audio out → assistant context
    pipeline_log = PipelineLogger("pipeline")
    pipeline = Pipeline([
        transport.input(),
        stt,
        context_aggregator.user(),
        llm,
        tts,
        pipeline_log,
        transport.output(),
        context_aggregator.assistant(),
    ])

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
    )

    # ── Events ─────────────────────────────────────────────────
    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("Client connected")
        context.add_message({
            "role": "system",
            "content": "The user just connected. Greet them warmly but briefly — one sentence max. Be natural, like picking up a conversation with a friend.",
        })
        await task.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("Client disconnected")
        await task.cancel()

    # ── Run ─────────────────────────────────────────────────────
    runner = PipelineRunner(handle_sigint=runner_args.handle_sigint)
    logger.info(f"Buddy is ready! Open http://{config.SERVER_HOST}:{config.SERVER_PORT}/client")
    logger.info(f"  STT: whisper.cpp @ {config.WHISPER_SERVER_URL}")
    logger.info(f"  TTS: Kokoro (voice={config.KOKORO_VOICE})")
    logger.info(f"  LLM: Claude ({config.LLM_MODEL})")
    await runner.run(task)


async def bot(runner_args: RunnerArguments):
    """Main entry point for Pipecat's runner."""
    transport_params = {
        "webrtc": lambda: TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
        ),
    }
    transport = await create_transport(runner_args, transport_params)
    await run_bot(transport, runner_args)


if __name__ == "__main__":
    from pipecat.runner.run import main
    main()
