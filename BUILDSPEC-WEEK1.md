# BUILDSPEC — Week 1: Voice Pipeline MVP (Local-First)

> **Audience:** AI coding agent (Claude Code, Cursor, Copilot, etc.)
> **Do not skip steps.** Each phase builds on the previous one. Verify each phase works before moving to the next.
> **Repo:** `git@github.com:defsan/buddy.git` (already cloned)
> **Working directory:** `/Users/elie/.openclaw/workspace/projects/buddy`
> **Host machine:** Mac Mini M4 (16GB), macOS, Python 3.12+, `uv` package manager available
> **Inference machine:** Mac Studio Ultra M4 (128GB) at `192.168.68.99` — runs whisper.cpp for STT

---

## GOAL

Build a voice companion server that:
1. Accepts audio input from a browser client over WebRTC
2. Transcribes speech to text using **whisper.cpp** running on Mac Studio (local, free)
3. Generates a response using **Anthropic Claude** (claude-sonnet-4-5)
4. Synthesizes speech using **Piper TTS** running on Mac Mini (local, free)
5. Streams audio back to the browser client
6. Supports natural interruption (user can talk while bot is speaking)
7. Accessible from any device on the LAN (Mac, iPhone, iPad)

**No cloud STT or TTS services needed.** Only paid service is Anthropic for the LLM.

**Success metric:** User opens `http://<mac-mini-ip>:7860/client` in a browser, speaks, and hears a conversational response within ~1.5 seconds.

---

## FILE STRUCTURE

Create this exact structure. Do not deviate.

```
projects/buddy/
├── BUILDSPEC-WEEK1.md          # This file (already exists)
├── plan.md                     # Already exists
├── scenario-c.md               # Already exists
├── week1.md                    # Already exists
├── scripts/
│   ├── install-whisper.sh      # Install whisper.cpp on Mac Studio
│   └── install-piper.sh        # Install Piper TTS on Mac Mini
├── server/
│   ├── pyproject.toml          # Python project config (uv-compatible)
│   ├── .env.example            # Template for config
│   ├── .env                    # Actual config (gitignored)
│   ├── bot.py                  # Main entry point — Pipecat pipeline
│   ├── config.py               # Configuration loader
│   ├── stt_whisper.py          # Whisper.cpp STT integration
│   └── tts_piper.py            # Piper TTS integration
├── .gitignore                  # Standard Python + .env
└── README.md                   # Setup and run instructions
```

---

## PHASE 0: Install Local STT + TTS

### 0.1 Install whisper.cpp on Mac Studio

whisper.cpp runs on the Mac Studio (128GB RAM, M4 Ultra) and serves an HTTP API. The Mac Mini sends audio to it over the LAN.

Create `scripts/install-whisper.sh`:

```bash
#!/bin/bash
# Install whisper.cpp with HTTP server on Mac Studio.
# Run this script ON the Mac Studio (192.168.68.99), not the Mac Mini.

set -e

INSTALL_DIR="$HOME/whisper.cpp"
MODEL="large-v3"

echo "📦 Installing whisper.cpp..."

# Clone if not present
if [ ! -d "$INSTALL_DIR" ]; then
    git clone https://github.com/ggerganov/whisper.cpp.git "$INSTALL_DIR"
else
    cd "$INSTALL_DIR" && git pull
fi

cd "$INSTALL_DIR"

# Build with Metal (Apple Silicon GPU acceleration)
cmake -B build -DWHISPER_METAL=ON
cmake --build build --config Release -j$(sysctl -n hw.ncpu)

# Download model
bash models/download-ggml-model.sh $MODEL

echo ""
echo "✅ whisper.cpp installed at $INSTALL_DIR"
echo ""
echo "To run the HTTP server:"
echo "  cd $INSTALL_DIR"
echo "  ./build/bin/whisper-server -m models/ggml-$MODEL.bin --host 0.0.0.0 --port 8178"
echo ""
echo "Test:"
echo "  curl http://localhost:8178/inference -F file=@test.wav"
```

**To run whisper.cpp as a persistent server on the Mac Studio:**
```bash
ssh 192.168.68.99
cd ~/whisper.cpp
./build/bin/whisper-server -m models/ggml-large-v3.bin --host 0.0.0.0 --port 8178
```

whisper-large-v3 on M4 Ultra runs at ~10x realtime — a 3-second utterance transcribes in ~300ms.

### 0.2 Install Piper TTS on Mac Mini

Piper runs locally on the Mac Mini. It's a fast, lightweight TTS engine that generates audio in ~50ms for short sentences.

Create `scripts/install-piper.sh`:

```bash
#!/bin/bash
# Install Piper TTS on Mac Mini (the Buddy server host).

set -e

INSTALL_DIR="$HOME/.local/share/piper"
VOICE="en_US-amy-medium"

echo "📦 Installing Piper TTS..."

mkdir -p "$INSTALL_DIR"
cd "$INSTALL_DIR"

# Download Piper binary for macOS ARM64
# Check https://github.com/rhasspy/piper/releases for latest
PIPER_VERSION="2023.11.14-2"
ARCH="macos_arm64"

if [ ! -f "piper/piper" ]; then
    echo "Downloading Piper binary..."
    curl -L -o piper.tar.gz \
        "https://github.com/rhasspy/piper/releases/download/${PIPER_VERSION}/piper_${ARCH}.tar.gz"
    tar xzf piper.tar.gz
    rm piper.tar.gz
    chmod +x piper/piper
    echo "✅ Piper binary installed"
else
    echo "Piper binary already exists"
fi

# Download voice model
VOICES_DIR="$INSTALL_DIR/voices"
mkdir -p "$VOICES_DIR"

if [ ! -f "$VOICES_DIR/${VOICE}.onnx" ]; then
    echo "Downloading voice model: $VOICE..."
    curl -L -o "$VOICES_DIR/${VOICE}.onnx" \
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium/en_US-amy-medium.onnx"
    curl -L -o "$VOICES_DIR/${VOICE}.onnx.json" \
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium/en_US-amy-medium.onnx.json"
    echo "✅ Voice model downloaded"
else
    echo "Voice model already exists"
fi

echo ""
echo "✅ Piper TTS installed at $INSTALL_DIR"
echo ""
echo "Test:"
echo "  echo 'Hello, this is a test.' | $INSTALL_DIR/piper/piper --model $VOICES_DIR/${VOICE}.onnx --output_file /tmp/test.wav"
echo "  afplay /tmp/test.wav"
```

**Alternative voices:** Browse https://rhasspy.github.io/piper-samples/ to find a voice. `amy-medium` is a solid default. `lessac-medium` and `libritts_r-medium` are also good.

### 0.3 Verify both services

From the Mac Mini:

```bash
# Test whisper.cpp on Mac Studio (record a short WAV first, or use any test file)
curl http://192.168.68.99:8178/inference -F file=@test.wav -F language=en -F response_format=json

# Test Piper on Mac Mini
echo "Hello, I am Buddy." | ~/.local/share/piper/piper/piper \
  --model ~/.local/share/piper/voices/en_US-amy-medium.onnx \
  --output_file /tmp/test.wav
afplay /tmp/test.wav
```

**If either is not installed yet:** The bot should print a clear error at startup pointing to the install scripts. Do not proceed to Phase 1 until both work.

---

## PHASE 1: Project Setup

### 1.1 Create `.gitignore`

```gitignore
# Python
__pycache__/
*.pyc
*.pyo
.venv/
venv/
*.egg-info/
dist/
build/

# Environment
.env
.env.local

# IDE
.vscode/
.idea/
*.swp
*.swo

# OS
.DS_Store
Thumbs.db

# Logs
logs/
*.log
```

### 1.2 Create `server/pyproject.toml`

```toml
[project]
name = "buddy"
version = "0.1.0"
description = "Voice companion — local-first Pipecat voice AI agent"
requires-python = ">=3.10"
dependencies = [
    "pipecat-ai[webrtc,anthropic,silero]",
    "python-dotenv",
    "loguru",
    "aiohttp",
]

[build-system]
requires = ["setuptools"]
build-backend = "setuptools.backends._legacy:_Backend"
```

**Note:** No `deepgram` or `elevenlabs` extras needed. We use `aiohttp` to call whisper.cpp's HTTP API instead.

### 1.3 Create `server/.env.example`

```ini
# Anthropic — https://console.anthropic.com
ANTHROPIC_API_KEY=your_anthropic_api_key_here

# Whisper.cpp server on Mac Studio
WHISPER_SERVER_URL=http://192.168.68.99:8178

# Piper TTS (local on Mac Mini)
# Auto-detected at ~/.local/share/piper/ if not set
# PIPER_BINARY=/path/to/piper
# PIPER_MODEL=/path/to/voice.onnx

# Server settings
# BUDDY_HOST=0.0.0.0
# BUDDY_PORT=7860
# BUDDY_LLM_MODEL=claude-sonnet-4-5-20250929
```

### 1.4 Create `server/.env`

Populate with real config. The Anthropic key is the only external API key needed.

| Key | Source |
|-----|--------|
| `ANTHROPIC_API_KEY` | Read from `~/.config/anthropic/api_key` if it exists, OR from OpenClaw config: `cat /Users/elie/.openclaw/openclaw.json` → `auth.profiles` → `anthropic:rellie`. |
| `WHISPER_SERVER_URL` | `http://192.168.68.99:8178` (Mac Studio running whisper.cpp) |
| `PIPER_BINARY` | Auto-detected. Override only if installed elsewhere. |
| `PIPER_MODEL` | Auto-detected. Override only if using a different voice. |

### 1.5 Create `server/config.py`

```python
"""Configuration loader for Buddy voice server."""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the server directory
_server_dir = Path(__file__).parent
load_dotenv(_server_dir / ".env", override=True)


def _require_key(name: str, hint: str) -> str:
    """Get a required environment variable or exit with a helpful message."""
    value = os.getenv(name, "").strip()
    if not value or value.startswith("your_"):
        print(f"\n❌  Missing required key: {name}")
        print(f"    → {hint}")
        print(f"    Set it in: {_server_dir / '.env'}\n")
        sys.exit(1)
    return value


# ── LLM (only paid service) ──────────────────────────────
ANTHROPIC_API_KEY = _require_key(
    "ANTHROPIC_API_KEY",
    "Get from https://console.anthropic.com"
)
LLM_MODEL = os.getenv("BUDDY_LLM_MODEL", "claude-sonnet-4-5-20250929")

# ── STT: Whisper.cpp on Mac Studio ───────────────────────
WHISPER_SERVER_URL = os.getenv("WHISPER_SERVER_URL", "http://192.168.68.99:8178")

# ── TTS: Piper on Mac Mini ──────────────────────────────
_home = Path.home()
PIPER_BINARY = os.getenv("PIPER_BINARY", str(_home / ".local/share/piper/piper/piper"))
PIPER_MODEL = os.getenv("PIPER_MODEL", str(_home / ".local/share/piper/voices/en_US-amy-medium.onnx"))

# Verify Piper exists at startup
if not Path(PIPER_BINARY).is_file():
    print(f"\n❌  Piper TTS binary not found at: {PIPER_BINARY}")
    print(f"    → Run: bash scripts/install-piper.sh")
    print(f"    Or set PIPER_BINARY in {_server_dir / '.env'}\n")
    sys.exit(1)

if not Path(PIPER_MODEL).is_file():
    print(f"\n❌  Piper voice model not found at: {PIPER_MODEL}")
    print(f"    → Run: bash scripts/install-piper.sh")
    print(f"    Or set PIPER_MODEL in {_server_dir / '.env'}\n")
    sys.exit(1)

# ── Server ───────────────────────────────────────────────
SERVER_HOST = os.getenv("BUDDY_HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("BUDDY_PORT", "7860"))
```

### 1.6 Verify setup

```bash
cd /Users/elie/.openclaw/workspace/projects/buddy/server
uv sync
```

This must complete without errors.

---

## PHASE 2: STT Integration (whisper.cpp)

### 2.1 Create `server/stt_whisper.py`

whisper.cpp is **batch** STT (not streaming). The flow:
1. Pipecat's VAD detects when user starts/stops speaking
2. Audio frames are buffered during speech
3. On speech end, the buffered audio is sent as a WAV file to whisper.cpp via HTTP
4. whisper.cpp returns transcription text
5. We emit a `TranscriptionFrame` into the pipeline

```python
"""Whisper.cpp STT integration for Pipecat.

Batch transcription: buffers audio during speech, sends to whisper.cpp
HTTP server on speech end, returns transcription.

whisper.cpp server runs on Mac Studio at http://192.168.68.99:8178.
whisper-large-v3 on M4 Ultra: ~300ms for a 3-second utterance.
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
from pipecat.processors.frame_processor import FrameProcessor


class WhisperSTTProcessor(FrameProcessor):
    """Buffers audio during speech, transcribes via whisper.cpp on speech end.
    
    This replaces Deepgram's streaming STT with a batch approach:
    - Listen for VAD speech start/stop frames
    - Buffer all AudioRawFrames during speech
    - On speech end, send buffered audio to whisper.cpp
    - Emit TranscriptionFrame with result
    
    Latency: ~300ms for typical utterances (3-5 seconds of speech)
    Cost: $0 (local inference on Mac Studio)
    """

    def __init__(
        self,
        server_url: str = "http://192.168.68.99:8178",
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
            # Start buffering audio
            self._is_speaking = True
            self._audio_buffer = []
            await self.push_frame(frame, direction)

        elif isinstance(frame, UserStoppedSpeakingFrame):
            # Speech ended — transcribe the buffered audio
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
            # Buffer audio during speech
            if self._is_speaking:
                self._audio_buffer.append(frame.audio)
            # Always pass audio frames through (for transport)
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
            # Combine buffered audio frames
            pcm_data = b"".join(self._audio_buffer)

            # Convert to WAV format (whisper.cpp expects a file)
            wav_bytes = self._pcm_to_wav(pcm_data, self._sample_rate)

            t0 = time.monotonic()
            session = await self._get_session()

            form = aiohttp.FormData()
            form.add_field(
                'file',
                wav_bytes,
                filename='audio.wav',
                content_type='audio/wav',
            )
            form.add_field('language', self._language)
            form.add_field('response_format', 'json')

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
            logger.error("Whisper server timeout — is it running on 192.168.68.99:8178?")
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
        # RIFF header
        buf.write(b'RIFF')
        buf.write(struct.pack('<I', 36 + data_size))
        buf.write(b'WAVE')
        # fmt chunk
        buf.write(b'fmt ')
        buf.write(struct.pack('<I', 16))
        buf.write(struct.pack('<H', 1))   # PCM format
        buf.write(struct.pack('<H', num_channels))
        buf.write(struct.pack('<I', sample_rate))
        buf.write(struct.pack('<I', byte_rate))
        buf.write(struct.pack('<H', block_align))
        buf.write(struct.pack('<H', bits_per_sample))
        # data chunk
        buf.write(b'data')
        buf.write(struct.pack('<I', data_size))
        buf.write(pcm_data)
        return buf.getvalue()
```

**Implementation notes for the coding agent:**

1. The `TranscriptionFrame` constructor params may differ by Pipecat version. Check the actual class. It may just take `text` and `user_id`, or it may require different fields. Adjust as needed.

2. The `AudioRawFrame` contains `.audio` (bytes) — this is raw PCM. If the field is named differently in your Pipecat version (e.g., `.data` or `.audio_data`), adjust accordingly.

3. The whisper.cpp server endpoint may be `/inference` or `/v1/audio/transcriptions` depending on how it was built. Test with `curl` first.

4. This processor must be placed AFTER the transport input and AFTER the VAD in the pipeline. The VAD emits `UserStartedSpeakingFrame` / `UserStoppedSpeakingFrame` which this processor listens for.

---

## PHASE 3: TTS Integration (Piper)

### 3.1 Create `server/tts_piper.py`

Piper is **batch** TTS — it generates a complete WAV file, which we convert to PCM frames for the pipeline.

```python
"""Piper TTS integration for Pipecat.

Generates speech locally using Piper on the Mac Mini.
~50ms for short sentences. Zero cost, fully private.

Piper produces WAV files via subprocess. We convert to raw PCM
AudioRawFrames for the Pipecat pipeline.
"""

import asyncio
import os
import struct
import tempfile
import time
from pathlib import Path

from loguru import logger

from pipecat.frames.frames import (
    AudioRawFrame,
    EndFrame,
    TextFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
)
from pipecat.processors.frame_processor import FrameProcessor


class PiperTTSProcessor(FrameProcessor):
    """Converts text to speech using Piper TTS.
    
    Receives TextFrames from the LLM, generates audio via Piper subprocess,
    and emits AudioRawFrames for the transport to play.
    
    Latency: ~50ms for short sentences
    Cost: $0 (local CPU inference)
    """

    def __init__(
        self,
        piper_binary: str,
        model_path: str,
        sample_rate: int = 16000,
    ):
        super().__init__()
        self._piper = piper_binary
        self._model = model_path
        self._sample_rate = sample_rate

        # Verify Piper exists
        if not os.path.isfile(self._piper):
            raise FileNotFoundError(f"Piper binary not found: {self._piper}")
        if not os.path.isfile(self._model):
            raise FileNotFoundError(f"Piper model not found: {self._model}")

        logger.info(f"✅ Piper TTS ready: {Path(self._model).stem}")

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

            # Emit TTS started marker
            await self.push_frame(TTSStartedFrame())

            # Run Piper as subprocess
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
                output_path = f.name

            proc = await asyncio.create_subprocess_exec(
                self._piper,
                '--model', self._model,
                '--output_file', output_path,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=text.encode('utf-8')),
                timeout=10.0,
            )

            if proc.returncode != 0:
                logger.error(f"Piper TTS failed: {stderr.decode()}")
                return

            # Read WAV file
            wav_data = Path(output_path).read_bytes()
            os.unlink(output_path)

            if len(wav_data) <= 44:
                logger.error("Piper produced empty audio")
                return

            # Extract raw PCM from WAV (skip 44-byte header)
            # Note: proper WAV parsing would read the header to find data offset.
            # The 44-byte assumption works for standard Piper output.
            pcm_data = wav_data[44:]

            elapsed = (time.monotonic() - t0) * 1000
            logger.debug(f"🔊 Piper TTS: {elapsed:.0f}ms for: {text[:60]}...")

            # Get the actual sample rate from WAV header
            wav_sample_rate = struct.unpack_from('<I', wav_data, 24)[0]

            # Emit audio as a single frame
            # Pipecat's transport will handle chunking for WebRTC
            await self.push_frame(AudioRawFrame(
                audio=pcm_data,
                sample_rate=wav_sample_rate,
                num_channels=1,
            ))

            # Emit TTS stopped marker
            await self.push_frame(TTSStoppedFrame())

        except asyncio.TimeoutError:
            logger.error("Piper TTS timeout")
        except Exception as e:
            logger.error(f"Piper TTS error: {e}")
```

**Implementation notes for the coding agent:**

1. `AudioRawFrame` constructor may differ by Pipecat version. It may take `audio`, `data`, or `bytes` as the PCM data param. Check the actual class definition. It may also require `sample_rate` and `num_channels` or these may be separate frame types.

2. `TTSStartedFrame` and `TTSStoppedFrame` may not exist in all Pipecat versions, or may be named differently. These are important for the pipeline's interruption handling — when the user speaks while TTS is playing, Pipecat uses these markers to know it should cancel TTS. If they don't exist, check for equivalent frame types.

3. Piper's output WAV sample rate depends on the voice model. `amy-medium` outputs at 22050 Hz. If the pipeline expects 16000 Hz, you may need to resample. Check if Pipecat's transport handles resampling, or add `--output-raw` to Piper with explicit sample rate settings.

4. For lower latency, consider piping Piper's output directly instead of using a temp file:
   ```python
   proc = await asyncio.create_subprocess_exec(
       self._piper, '--model', self._model, '--output-raw',
       stdin=PIPE, stdout=PIPE, stderr=PIPE,
   )
   ```
   Then read raw PCM from stdout. This avoids disk I/O.

---

## PHASE 4: Bot Server

### 4.1 Create `server/bot.py`

This is the main entry point. It creates a Pipecat pipeline with:
- **SmallWebRTCTransport** — peer-to-peer WebRTC with built-in dev web client
- **SileroVADAnalyzer** — voice activity detection for turn-taking
- **WhisperSTTProcessor** — batch STT via whisper.cpp on Mac Studio
- **AnthropicLLMService** — Claude for conversation
- **PiperTTSProcessor** — local TTS via Piper on Mac Mini
- **LLMContextAggregatorPair** — manages conversation history

```python
"""Buddy — Voice Companion Server (Local-First).

Uses whisper.cpp (Mac Studio) for STT and Piper (local) for TTS.
Only paid service is Anthropic Claude for the LLM.

Run with: uv run bot.py
Then open http://localhost:7860/client in your browser.
"""

import os
import sys

# Ensure server directory is in path for config import
sys.path.insert(0, os.path.dirname(__file__))

from loguru import logger

print("🐾 Starting Buddy voice companion (local-first)...")
print("⏳ Loading models...\n")

logger.info("Loading Silero VAD model...")
from pipecat.audio.vad.silero import SileroVADAnalyzer
logger.info("✅ Silero VAD loaded")

from pipecat.frames.frames import LLMRunFrame
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
from tts_piper import PiperTTSProcessor

logger.info("✅ All components loaded")

import config  # Validates config on import

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

    tts = PiperTTSProcessor(
        piper_binary=config.PIPER_BINARY,
        model_path=config.PIPER_MODEL,
    )

    # ── Conversation Context ───────────────────────────────────
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
    ]

    context = LLMContext(messages)
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(),
        ),
    )

    # ── Pipeline ───────────────────────────────────────────────
    # Data flows left-to-right:
    #   audio in → STT (whisper.cpp) → user context → LLM (Claude) → TTS (Piper) → audio out → assistant context
    pipeline = Pipeline([
        transport.input(),       # Receive audio from browser via WebRTC
        stt,                     # whisper.cpp: audio → text (batch, ~300ms)
        user_aggregator,         # Collect user text into conversation context
        llm,                     # Claude: generate response (streaming)
        tts,                     # Piper: text → audio (batch, ~50ms)
        transport.output(),      # Send audio back to browser via WebRTC
        assistant_aggregator,    # Collect bot response into conversation context
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
        logger.info("🔗 Client connected")
        messages.append({
            "role": "system",
            "content": "The user just connected. Greet them warmly but briefly — one sentence max. Be natural, like picking up a conversation with a friend.",
        })
        await task.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("👋 Client disconnected")
        await task.cancel()

    # ── Run ─────────────────────────────────────────────────────
    runner = PipelineRunner(handle_sigint=runner_args.handle_sigint)
    logger.info(f"🐾 Buddy is ready! Open http://{config.SERVER_HOST}:{config.SERVER_PORT}/client")
    logger.info(f"   STT: whisper.cpp @ {config.WHISPER_SERVER_URL}")
    logger.info(f"   TTS: Piper @ {config.PIPER_BINARY}")
    logger.info(f"   LLM: Claude ({config.LLM_MODEL})")
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
```

**Key notes:**
- `pipecat.runner.run.main()` handles SmallWebRTCTransport setup and serves the dev client at `/client`
- The pipeline is the same architecture as cloud, just different STT/TTS processors
- Interruption handling works the same: VAD detects user speech → cancels in-flight TTS

### 4.2 Verify bot starts

```bash
cd /Users/elie/.openclaw/workspace/projects/buddy/server
uv run bot.py
```

Expected output:
```
🐾 Starting Buddy voice companion (local-first)...
⏳ Loading models...
... ✅ Silero VAD loaded
... ✅ Piper TTS ready: en_US-amy-medium
... ✅ All components loaded
... 🐾 Buddy is ready! Open http://0.0.0.0:7860/client
...    STT: whisper.cpp @ http://192.168.68.99:8178
...    TTS: Piper @ /Users/elie/.local/share/piper/piper/piper
...    LLM: Claude (claude-sonnet-4-5-20250929)
```

### 4.3 Test from browser

1. Ensure whisper.cpp server is running on Mac Studio
2. Open `http://localhost:7860/client` (or `http://<mac-mini-ip>:7860/client`)
3. Click "Connect"
4. Allow microphone access
5. Speak — you should hear Buddy respond

**Troubleshooting:**
- If no transcription: verify whisper.cpp server is running (`curl http://192.168.68.99:8178/`)
- If no audio response: check Piper is installed (`~/.local/share/piper/piper/piper --help`)
- If LLM not responding: verify Anthropic key
- If no audio from browser: check browser console for WebRTC errors

---

## PHASE 5: README

### 5.1 Create `README.md`

```markdown
# 🐾 Buddy — Voice Companion (Local-First)

Talk to Buddy from any browser on your network. Built with [Pipecat](https://pipecat.ai).

**Zero cloud STT/TTS costs.** Uses whisper.cpp and Piper TTS locally. Only paid service is Anthropic Claude for the brain.

## Quick Start

### 1. Prerequisites

| Component | Where | Install |
|-----------|-------|---------|
| whisper.cpp | Mac Studio (192.168.68.99) | `bash scripts/install-whisper.sh` |
| Piper TTS | Mac Mini (this machine) | `bash scripts/install-piper.sh` |
| Anthropic API key | [console.anthropic.com](https://console.anthropic.com) | Pay-as-you-go |

### 2. Start whisper.cpp server (on Mac Studio)

```bash
ssh 192.168.68.99
cd ~/whisper.cpp
./build/bin/whisper-server -m models/ggml-large-v3.bin --host 0.0.0.0 --port 8178
```

### 3. Configure (on Mac Mini)

```bash
cd server
cp .env.example .env
# Edit .env — add your ANTHROPIC_API_KEY
```

### 4. Install & Run

```bash
cd server
uv sync
uv run bot.py
```

### 5. Connect

Open `http://localhost:7860/client` in your browser. Click Connect. Talk.

From other devices on your LAN: `http://<mac-mini-ip>:7860/client`

## Architecture

```
Browser Mic → WebRTC → Silero VAD → whisper.cpp (Mac Studio) → Claude → Piper TTS (local) → WebRTC → Browser Speaker
```

## Cost

| Service | Monthly |
|---------|---------|
| whisper.cpp (local) | $0 |
| Piper TTS (local) | $0 |
| Claude API | ~$3-5 (varies with usage) |
| **Total** | **~$3-5/mo** |

## Configuration

Edit `server/.env`:

| Variable | Description | Default |
|----------|-------------|---------|
| `ANTHROPIC_API_KEY` | Anthropic API key | (required) |
| `WHISPER_SERVER_URL` | whisper.cpp server URL | `http://192.168.68.99:8178` |
| `PIPER_BINARY` | Path to Piper binary | auto-detected |
| `PIPER_MODEL` | Path to Piper voice model | auto-detected |
| `BUDDY_HOST` | Server bind address | `0.0.0.0` |
| `BUDDY_PORT` | Server port | `7860` |
| `BUDDY_LLM_MODEL` | Claude model | `claude-sonnet-4-5-20250929` |

## Changing Buddy's Voice

Browse voices at https://rhasspy.github.io/piper-samples/. Download the `.onnx` and `.onnx.json` files, save to `~/.local/share/piper/voices/`, and set `PIPER_MODEL` in `.env`.

## Changing Buddy's Personality

Edit the `SYSTEM_PROMPT` in `server/bot.py`.
```

---

## PHASE 6: Validation Checklist

All must pass before considering Week 1 complete.

- [ ] whisper.cpp server running on Mac Studio (`curl http://192.168.68.99:8178/` returns 200)
- [ ] Piper installed on Mac Mini (`~/.local/share/piper/piper/piper --help` works)
- [ ] `uv sync` completes without errors in `server/`
- [ ] `uv run bot.py` starts without errors
- [ ] Browser at `http://localhost:7860/client` shows a Connect button
- [ ] Clicking Connect establishes WebRTC connection (server logs: "Client connected")
- [ ] Speaking into mic produces transcription (server logs: "User said: ...")
- [ ] Claude generates a response (check server logs)
- [ ] Piper TTS plays audio back through browser
- [ ] Full round-trip: speak → hear response within ~2 seconds
- [ ] Interruption works: speak while Buddy is talking → Buddy stops and listens
- [ ] Accessible from iPhone Safari on same LAN: `http://<mac-mini-ip>:7860/client`
- [ ] Server runs stable for 10+ minutes of conversation without crashes
- [ ] `.env` is gitignored, no keys in committed code
- [ ] All files committed and pushed to `git@github.com:defsan/buddy.git`

---

## PHASE 7: Git

```bash
cd /Users/elie/.openclaw/workspace/projects/buddy
git add -A
git commit -m "Week 1: Voice pipeline MVP — local-first (whisper.cpp + Claude + Piper TTS)"
git push origin main
```

---

## KNOWN ISSUES & EDGE CASES

1. **Pipecat API changes:** Pipecat is actively developed. If import paths don't match (e.g., `pipecat.audio.vad.silero` vs `pipecat.vad.silero`), check the installed version's actual module structure with `python -c "import pipecat; print(pipecat.__version__)"` and adjust imports.

2. **whisper.cpp server binary name:** Depending on the build, the server binary may be `whisper-server`, `server`, or `main` in `build/bin/`. Check what's actually there.

3. **whisper.cpp API endpoint:** May be `/inference` or `/v1/audio/transcriptions`. Test with `curl` first.

4. **Piper output sample rate:** Varies by voice model. `amy-medium` outputs 22050 Hz. If the pipeline expects 16000 Hz, you may need resampling. Check if Pipecat's transport handles this automatically.

5. **Piper macOS ARM64 binary:** May not be available for all Piper versions. Check releases at https://github.com/rhasspy/piper/releases. If no ARM64 build, use x86 under Rosetta 2, or build from source.

6. **Batch STT latency:** whisper.cpp is batch, not streaming. It waits for the user to stop speaking before transcribing. This adds ~300ms vs streaming services, but the total experience is still natural because the silence gap at end-of-speech is where this processing happens.

7. **SmallWebRTCTransport binding:** Binds to `0.0.0.0` by default. If other LAN devices can't connect, check macOS firewall settings.

8. **iPhone Safari WebRTC:** May require HTTPS for microphone access when not on localhost. Try `http://mac-mini.local:7860/client` or set up Tailscale for HTTPS.

9. **Anthropic model name:** `claude-sonnet-4-5-20250929` is current as of Feb 2026. If it errors, check https://docs.anthropic.com/en/docs/about-claude/models for current names.

---

## WHAT COMES AFTER WEEK 1

These are NOT in scope for this buildspec:

- **Week 2:** Wire OpenClaw for memory/personality/tools, build iOS PWA client
- **Week 3:** iPad home station, wake word detection ("Hey Buddy")
- **Week 4:** Cloud upgrade option (Deepgram + ElevenLabs), production polish, monitoring
