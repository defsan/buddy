"""Whisper.cpp STT integration for Pipecat.

Batch transcription: buffers audio during speech, sends to whisper.cpp
HTTP server on speech end, returns transcription.

whisper.cpp server runs locally at http://127.0.0.1:8178.
"""

import asyncio
import io
import struct
import time

import aiohttp
from loguru import logger

from pipecat.frames.frames import (
    AudioRawFrame,
    EndFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class WhisperSTTProcessor(FrameProcessor):
    """Buffers audio during speech, transcribes via whisper.cpp on speech end."""

    def __init__(
        self,
        server_url: str = "http://127.0.0.1:8178",
        language: str = "en",
        sample_rate: int = 16000,
        timeout_seconds: float = 10.0,
    ):
        super().__init__()
        self._server_url = server_url.rstrip("/")
        self._language = language
        self._sample_rate = sample_rate
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._session: aiohttp.ClientSession | None = None

        # Audio buffering
        self._is_speaking = False
        self._audio_buffer: list[bytes] = []

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)

        if isinstance(frame, UserStartedSpeakingFrame):
            self._is_speaking = True
            self._audio_buffer = []
            await self.push_frame(frame, direction)

        elif isinstance(frame, UserStoppedSpeakingFrame):
            self._is_speaking = False
            await self.push_frame(frame, direction)

            if self._audio_buffer:
                text = await self._transcribe()
                if text:
                    logger.info(f"🎤 User said: {text}")
                    await self.push_frame(TranscriptionFrame(
                        text=text,
                        user_id="user",
                        timestamp=str(time.time()),
                    ))
                self._audio_buffer = []

        elif isinstance(frame, AudioRawFrame):
            if self._is_speaking:
                self._audio_buffer.append(frame.audio)
            await self.push_frame(frame, direction)

        elif isinstance(frame, EndFrame):
            await self._cleanup()
            await self.push_frame(frame, direction)

        else:
            await self.push_frame(frame, direction)

    async def _transcribe(self) -> str | None:
        """Send buffered audio to whisper.cpp and return transcription."""
        if not self._audio_buffer:
            return None

        try:
            pcm_data = b"".join(self._audio_buffer)
            wav_bytes = self._pcm_to_wav(pcm_data, self._sample_rate)

            t0 = time.monotonic()
            session = await self._get_session()

            form = aiohttp.FormData()
            form.add_field(
                "file",
                wav_bytes,
                filename="audio.wav",
                content_type="audio/wav",
            )
            form.add_field("language", self._language)
            form.add_field("response_format", "json")

            async with session.post(
                f"{self._server_url}/inference",
                data=form,
            ) as resp:
                if resp.status != 200:
                    logger.error(f"Whisper server returned {resp.status}: {await resp.text()}")
                    return None

                result = await resp.json()
                text = result.get("text", "").strip()
                elapsed = (time.monotonic() - t0) * 1000
                logger.debug(f"⏱️  Whisper STT: {elapsed:.0f}ms")
                return text or None

        except asyncio.TimeoutError:
            logger.error("Whisper server timeout — is it running on 127.0.0.1:8178?")
            return None
        except aiohttp.ClientError as e:
            logger.error(f"Whisper server connection error: {e}")
            return None
        except Exception as e:
            logger.error(f"Whisper transcription error: {e}")
            return None

    async def _cleanup(self):
        if self._session and not self._session.closed:
            await self._session.close()

    @staticmethod
    def _pcm_to_wav(pcm_data: bytes, sample_rate: int = 16000, bits_per_sample: int = 16) -> bytes:
        """Convert raw PCM bytes to WAV format."""
        num_channels = 1
        byte_rate = sample_rate * num_channels * bits_per_sample // 8
        block_align = num_channels * bits_per_sample // 8
        data_size = len(pcm_data)

        buf = io.BytesIO()
        buf.write(b"RIFF")
        buf.write(struct.pack("<I", 36 + data_size))
        buf.write(b"WAVE")
        buf.write(b"fmt ")
        buf.write(struct.pack("<I", 16))
        buf.write(struct.pack("<H", 1))  # PCM format
        buf.write(struct.pack("<H", num_channels))
        buf.write(struct.pack("<I", sample_rate))
        buf.write(struct.pack("<I", byte_rate))
        buf.write(struct.pack("<H", block_align))
        buf.write(struct.pack("<H", bits_per_sample))
        buf.write(b"data")
        buf.write(struct.pack("<I", data_size))
        buf.write(pcm_data)
        return buf.getvalue()
