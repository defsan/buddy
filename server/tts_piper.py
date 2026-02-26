"""Piper TTS integration for Pipecat.

Generates speech locally using the piper-tts Python package.
~100ms for short sentences. Zero cost, fully private.
"""

import time
from pathlib import Path

from loguru import logger
from piper import PiperVoice

from pipecat.frames.frames import (
    EndFrame,
    TTSAudioRawFrame,
    TextFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
)
from pipecat.processors.frame_processor import FrameProcessor


class PiperTTSProcessor(FrameProcessor):
    """Converts text to speech using Piper TTS (Python library).

    Receives TextFrames from the LLM, generates audio via PiperVoice,
    and emits AudioRawFrames for the transport to play.
    """

    def __init__(self, model_path: str):
        super().__init__()
        if not Path(model_path).is_file():
            raise FileNotFoundError(f"Piper model not found: {model_path}")

        self._voice = PiperVoice.load(model_path)
        self._sample_rate = self._voice.config.sample_rate
        logger.info(f"Piper TTS ready: {Path(model_path).stem} ({self._sample_rate}Hz)")

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)

        if isinstance(frame, TextFrame):
            text = frame.text.strip()
            if text:
                await self._synthesize_and_emit(text)

        elif isinstance(frame, EndFrame):
            await self.push_frame(frame, direction)

        else:
            await self.push_frame(frame, direction)

    async def _synthesize_and_emit(self, text: str):
        """Generate speech from text and emit as audio frames."""
        try:
            t0 = time.monotonic()

            await self.push_frame(TTSStartedFrame())

            # Synthesize all chunks and combine
            pcm_parts = []
            for chunk in self._voice.synthesize(text):
                pcm_parts.append(chunk.audio_int16_bytes)

            pcm_data = b"".join(pcm_parts)

            if not pcm_data:
                logger.error("Piper produced empty audio")
                return

            elapsed = (time.monotonic() - t0) * 1000
            logger.debug(f"Piper TTS: {elapsed:.0f}ms for: {text[:60]}...")

            await self.push_frame(TTSAudioRawFrame(
                audio=pcm_data,
                sample_rate=self._sample_rate,
                num_channels=1,
            ))

            await self.push_frame(TTSStoppedFrame())

        except Exception as e:
            logger.error(f"Piper TTS error: {e}")
