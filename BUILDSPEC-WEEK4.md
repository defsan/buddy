# BUILDSPEC — Week 4: Local Fallback Chain + Production Polish

> **Audience:** AI coding agent (Claude Code, Cursor, Copilot, etc.)
> **Prerequisite:** Week 3 complete — Wake word, iPad home station, multi-device, audio hardening all working.
> **Do not skip steps.** Each phase builds on the previous one. Verify each phase works before moving to the next.
> **Repo:** `git@github.com:defsan/buddy.git`
> **Working directory:** `/Users/elie/.openclaw/workspace/projects/buddy`
> **Host machine:** Mac Mini M4 (16GB), macOS, Python 3.12+, `uv` package manager
> **Local inference machine:** Mac Studio Ultra M4 (128GB RAM) at `192.168.68.99`
>   - Ollama running with `qwen3:30b` (100 tok/s, 18.6GB VRAM)
>   - Also available: `whisper-large-v3` via whisper.cpp
>   - Provider from Mac Mini: `http://192.168.68.99:11434`

---

## GOAL

Make Buddy resilient, fast, and production-ready:

1. **Local fallback chain:** When cloud APIs are down or slow, fall back to local models on Mac Studio — Buddy never goes silent
2. **Latency optimization:** Reduce end-to-end latency to <800ms (local) and <1.2s (cloud)
3. **Voice cloning (optional):** Custom voice via ElevenLabs voice cloning
4. **Process management:** Buddy runs as a launchd service, auto-starts on boot, auto-restarts on crash
5. **Monitoring:** Health dashboard, latency tracking, cost tracking, uptime metrics
6. **Production polish:** Error recovery, graceful degradation, edge case handling

**Success metric:** Pull the internet cable from the Mac Mini. Say "Hey Buddy, what time is it?" — hear a response within 1 second, entirely from local models. Plug internet back in — Buddy seamlessly returns to cloud models.

---

## FILE STRUCTURE (additions to Week 3)

```
projects/buddy/
├── server/
│   ├── bot.py                  # Updated: fallback chain integration
│   ├── openclaw_llm.py         # Updated: streaming, smarter fallback
│   ├── fallback.py             # NEW: local model fallback chain
│   ├── stt_local.py            # NEW: Whisper.cpp STT fallback
│   ├── tts_local.py            # NEW: Piper TTS fallback
│   ├── health.py               # NEW: health check endpoint + metrics
│   ├── device_manager.py       # Unchanged from Week 3
│   ├── latency.py              # Updated: richer metrics, persistence
│   ├── config.py               # Updated: local model settings
│   ├── pyproject.toml          # Updated: add new deps
│   └── .env                    # Updated: local model config
├── client/
│   └── web/
│       ├── index.html          # Updated: connection quality indicator
│       ├── app.js              # Updated: latency display, health indicator
│       ├── style.css           # Updated: quality indicator styles
│       └── ... (rest unchanged from Week 3)
├── scripts/
│   ├── install-piper.sh        # NEW: install Piper TTS on Mac Mini
│   ├── install-whisper.sh      # NEW: install whisper.cpp on Mac Studio
│   ├── buddy.plist             # NEW: launchd service definition
│   └── install-service.sh      # NEW: install launchd service
├── docs/
│   ├── ipad-setup.md           # Unchanged from Week 3
│   ├── fallback-chain.md       # NEW: fallback architecture docs
│   └── monitoring.md           # NEW: health/metrics docs
└── README.md                   # Updated: final comprehensive README
```

---

## PHASE 1: Local STT Fallback (Whisper.cpp)

### 1.1 Install whisper.cpp on Mac Studio

Whisper.cpp runs on the Mac Studio (128GB RAM, M4 Ultra). The Mac Mini calls it over HTTP.

Create `scripts/install-whisper.sh`:

```bash
#!/bin/bash
# Install whisper.cpp with server mode on Mac Studio.
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

**The whisper.cpp server** exposes an HTTP API compatible with OpenAI's `/v1/audio/transcriptions` endpoint. The Mac Mini sends audio files to `http://192.168.68.99:8178/inference` and gets back transcription text.

**Alternative:** If whisper.cpp server mode is too much setup, use Ollama's whisper support or a simpler approach: the Mac Mini sends audio to Mac Studio via a lightweight HTTP endpoint that runs whisper.cpp CLI on each chunk.

### 1.2 Create `server/stt_local.py`

```python
"""Local STT fallback using whisper.cpp on Mac Studio.

When Deepgram is unavailable or too slow, falls back to whisper.cpp
running on the Mac Studio at 192.168.68.99:8178.

whisper-large-v3 on M4 Ultra runs at ~10x realtime — a 3-second
utterance transcribes in ~300ms.

This is NOT streaming (unlike Deepgram). It transcribes complete
audio chunks after VAD detects end-of-speech. This adds ~300ms
latency vs Deepgram's streaming, but it's zero-cost and works offline.
"""

import asyncio
import io
import struct
import tempfile
from pathlib import Path

import aiohttp
from loguru import logger


class WhisperLocalSTT:
    """Sends audio to whisper.cpp HTTP server for transcription."""

    def __init__(
        self,
        server_url: str = "http://192.168.68.99:8178",
        model: str = "large-v3",
        language: str = "en",
        timeout_seconds: float = 10.0,
    ):
        self._server_url = server_url.rstrip("/")
        self._model = model
        self._language = language
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._session: aiohttp.ClientSession | None = None
        self._healthy = True

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    async def transcribe(self, audio_pcm: bytes, sample_rate: int = 16000) -> str | None:
        """Transcribe PCM audio bytes. Returns text or None on failure.
        
        Args:
            audio_pcm: Raw PCM audio (16-bit signed, mono)
            sample_rate: Sample rate (default 16000)
        """
        if not self._healthy:
            # Try again after failures (reset every call for simplicity)
            self._healthy = True

        try:
            # Convert PCM to WAV (whisper.cpp expects a file)
            wav_bytes = self._pcm_to_wav(audio_pcm, sample_rate)

            session = await self._get_session()

            # whisper.cpp server endpoint
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
                    logger.error(f"Whisper server returned {resp.status}")
                    self._healthy = False
                    return None

                result = await resp.json()
                text = result.get("text", "").strip()
                if text:
                    logger.info(f"🎤 [whisper.cpp] {text}")
                return text or None

        except asyncio.TimeoutError:
            logger.error("Whisper server timeout")
            self._healthy = False
            return None
        except aiohttp.ClientError as e:
            logger.error(f"Whisper server connection error: {e}")
            self._healthy = False
            return None
        except Exception as e:
            logger.error(f"Whisper transcription error: {e}")
            self._healthy = False
            return None

    async def health_check(self) -> bool:
        """Check if whisper.cpp server is reachable."""
        try:
            session = await self._get_session()
            async with session.get(f"{self._server_url}/", timeout=aiohttp.ClientTimeout(total=3)) as resp:
                self._healthy = resp.status == 200
                return self._healthy
        except Exception:
            self._healthy = False
            return False

    async def cleanup(self):
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
        buf.write(struct.pack('<I', 16))  # chunk size
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

**Implementation notes:**

1. whisper.cpp server's exact API may differ from what's shown. Check the actual server's endpoints. Common patterns:
   - POST `/inference` with multipart form (file + params)
   - POST `/v1/audio/transcriptions` (OpenAI-compatible mode)
   
2. The PCM-to-WAV conversion is needed because whisper.cpp expects a file, not raw PCM. If the server accepts raw PCM, skip the conversion.

3. This is batch transcription (not streaming). Pipecat's STT pipeline normally uses streaming Deepgram. To integrate batch whisper, you need a custom `FrameProcessor` that:
   - Buffers audio frames after VAD detects speech start
   - On VAD speech end: sends the buffered audio to whisper
   - Emits a single `TranscriptionFrame` with the full text
   
   See Phase 2 for the fallback chain integration.

### 1.3 Verify whisper.cpp

SSH into Mac Studio and start the server:
```bash
ssh 192.168.68.99
cd ~/whisper.cpp
./build/bin/whisper-server -m models/ggml-large-v3.bin --host 0.0.0.0 --port 8178
```

From Mac Mini, test:
```bash
# Record a short test clip (or use any WAV file)
# Then:
curl http://192.168.68.99:8178/inference \
  -F file=@test.wav \
  -F language=en \
  -F response_format=json
```

Expected: JSON with `"text": "your transcribed words"`.

**If whisper.cpp is not set up yet:** The fallback chain should still work — it will skip whisper and use Deepgram only. Print a warning at startup.

---

## PHASE 2: Local TTS Fallback (Piper)

### 2.1 Install Piper TTS on Mac Mini

Piper runs locally on the Mac Mini (no GPU needed — CPU is fast enough for TTS).

Create `scripts/install-piper.sh`:

```bash
#!/bin/bash
# Install Piper TTS on Mac Mini.
# Piper is a fast, lightweight local TTS engine.

set -e

INSTALL_DIR="$HOME/.local/share/piper"
VOICE="en_US-amy-medium"  # Good default female voice

echo "📦 Installing Piper TTS..."

mkdir -p "$INSTALL_DIR"
cd "$INSTALL_DIR"

# Download Piper binary for macOS ARM64
# Check https://github.com/rhasspy/piper/releases for latest
PIPER_VERSION="2023.11.14-2"
ARCH="macos_arm64"

if [ ! -f "piper" ]; then
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
echo "  echo 'Hello, this is a test.' | $INSTALL_DIR/piper/piper --model $VOICES_DIR/${VOICE}.onnx --output_file test.wav"
echo "  afplay test.wav"
```

**Alternative voices:** Browse https://rhasspy.github.io/piper-samples/ to find a voice that sounds good. The `amy-medium` model is a solid default. `lessac-medium` or `libritts_r-medium` are also good.

### 2.2 Create `server/tts_local.py`

```python
"""Local TTS fallback using Piper.

When ElevenLabs is unavailable, falls back to Piper TTS running
locally on the Mac Mini. Piper generates audio in ~50ms for short
sentences — much faster than cloud TTS.

Quality is noticeably lower than ElevenLabs but perfectly usable
for a voice companion. The latency improvement partially compensates.
"""

import asyncio
import os
import tempfile
from pathlib import Path

from loguru import logger


class PiperLocalTTS:
    """Generate speech using Piper TTS (local, CPU-based)."""

    def __init__(
        self,
        piper_binary: str | None = None,
        model_path: str | None = None,
        output_sample_rate: int = 16000,
    ):
        # Auto-detect installation paths
        home = Path.home()
        default_piper = home / ".local/share/piper/piper/piper"
        default_model = home / ".local/share/piper/voices/en_US-amy-medium.onnx"

        self._piper = piper_binary or str(default_piper)
        self._model = model_path or str(default_model)
        self._sample_rate = output_sample_rate
        self._available = False

        # Check if Piper is installed
        if os.path.isfile(self._piper) and os.path.isfile(self._model):
            self._available = True
            logger.info(f"✅ Piper TTS available: {self._model}")
        else:
            logger.warning(f"⚠️ Piper TTS not found at {self._piper}")
            logger.warning(f"   Run scripts/install-piper.sh to install")

    @property
    def available(self) -> bool:
        return self._available

    async def synthesize(self, text: str) -> bytes | None:
        """Convert text to PCM audio bytes. Returns raw 16-bit PCM or None.
        
        Args:
            text: Text to speak
            
        Returns:
            Raw PCM bytes (16-bit signed, mono, at output_sample_rate) or None
        """
        if not self._available:
            return None

        try:
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
                output_path = f.name

            # Run Piper as subprocess
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
                logger.error(f"Piper failed: {stderr.decode()}")
                return None

            # Read WAV and extract PCM data (skip 44-byte WAV header)
            wav_data = Path(output_path).read_bytes()
            os.unlink(output_path)

            if len(wav_data) <= 44:
                logger.error("Piper produced empty audio")
                return None

            # Extract raw PCM (skip WAV header)
            # This is a simplification — proper WAV parsing would read the header
            pcm_data = wav_data[44:]
            logger.debug(f"🔊 [piper] Generated {len(pcm_data)} bytes for: {text[:50]}...")
            return pcm_data

        except asyncio.TimeoutError:
            logger.error("Piper TTS timeout")
            return None
        except Exception as e:
            logger.error(f"Piper TTS error: {e}")
            return None

    async def health_check(self) -> bool:
        """Quick check that Piper can generate audio."""
        if not self._available:
            return False
        result = await self.synthesize("test")
        return result is not None and len(result) > 0
```

### 2.3 Verify Piper

```bash
# Run install script first
bash scripts/install-piper.sh

# Test
echo "Hello, I am Buddy, your voice companion." | \
  ~/.local/share/piper/piper/piper \
  --model ~/.local/share/piper/voices/en_US-amy-medium.onnx \
  --output_file /tmp/test.wav

afplay /tmp/test.wav
```

Expected: Hear a clear, natural-sounding voice. ~50ms generation time for short sentences.

**If Piper is not installed yet:** The fallback chain skips it. Print a warning at startup.

---

## PHASE 3: Local LLM Fallback (Qwen via Ollama)

### 3.1 Verify Ollama + Qwen

Qwen 3 30B is already running on the Mac Studio via Ollama. Verify:

```bash
curl http://192.168.68.99:11434/api/chat -d '{
  "model": "qwen3:30b",
  "messages": [{"role": "user", "content": "Say hello in one sentence."}],
  "stream": false
}'
```

Expected: JSON response with a short greeting. Response time ~200ms for first token.

### 3.2 The Qwen integration already exists via OpenClaw

OpenClaw already has `ollama-local` configured as a provider pointing to `192.168.68.99:11434`. The fallback LLM path is:

1. **Primary:** Claude via OpenClaw (cloud) — best quality, has tools/memory
2. **Fallback A:** Direct Anthropic (cloud, no tools) — from Week 2
3. **Fallback B:** Qwen 30B via Ollama (local, no tools) — new in Week 4

For Fallback B, we call Ollama's API directly (not through OpenClaw) to avoid any gateway dependency.

---

## PHASE 4: Fallback Chain Orchestrator

### 4.1 Create `server/fallback.py`

This is the core of Week 4 — an intelligent fallback chain that routes through the best available service.

```python
"""Intelligent fallback chain for all voice pipeline components.

Routes STT/LLM/TTS through the best available service with automatic
failover. Monitors health of each service and switches seamlessly.

Chain priority:
  STT:  Deepgram (cloud, streaming) → Whisper.cpp (local, batch)
  LLM:  OpenClaw+Claude (cloud, tools) → Direct Claude (cloud, no tools) → Qwen 30B (local)
  TTS:  ElevenLabs (cloud, streaming) → Piper (local, batch)

Health checks run in background every 30s. When a primary service
recovers, we switch back to it automatically.
"""

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import aiohttp
from loguru import logger


class ServiceStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"   # Working but slow
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass
class ServiceHealth:
    name: str
    status: ServiceStatus = ServiceStatus.UNKNOWN
    last_check: float = 0.0
    last_success: float = 0.0
    last_failure: float = 0.0
    failure_count: int = 0
    avg_latency_ms: float = 0.0
    _latencies: list[float] = field(default_factory=list)

    def record_success(self, latency_ms: float):
        self.status = ServiceStatus.HEALTHY
        self.last_success = time.time()
        self.last_check = time.time()
        self.failure_count = 0
        self._latencies.append(latency_ms)
        if len(self._latencies) > 20:
            self._latencies = self._latencies[-20:]
        self.avg_latency_ms = sum(self._latencies) / len(self._latencies)

    def record_failure(self):
        self.failure_count += 1
        self.last_failure = time.time()
        self.last_check = time.time()
        if self.failure_count >= 3:
            self.status = ServiceStatus.UNHEALTHY
        else:
            self.status = ServiceStatus.DEGRADED

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status.value,
            "failure_count": self.failure_count,
            "avg_latency_ms": round(self.avg_latency_ms, 1),
            "last_success_ago": f"{time.time() - self.last_success:.0f}s" if self.last_success else "never",
        }


class FallbackChain:
    """Manages service health and automatic failover.
    
    Usage:
        chain = FallbackChain(config)
        await chain.start_health_checks()
        
        # Get best available STT service
        stt = chain.best_stt()  # Returns 'deepgram' or 'whisper'
        
        # Get best available LLM service  
        llm = chain.best_llm()  # Returns 'openclaw', 'anthropic', or 'qwen'
        
        # Get best available TTS service
        tts = chain.best_tts()  # Returns 'elevenlabs' or 'piper'
    """

    def __init__(self, config):
        self.config = config
        self._health_task: asyncio.Task | None = None

        # Service health tracking
        self.services = {
            # STT
            'deepgram': ServiceHealth(name='Deepgram STT'),
            'whisper': ServiceHealth(name='Whisper.cpp STT'),
            # LLM
            'openclaw': ServiceHealth(name='OpenClaw + Claude'),
            'anthropic': ServiceHealth(name='Direct Anthropic'),
            'qwen': ServiceHealth(name='Qwen 30B (local)'),
            # TTS
            'elevenlabs': ServiceHealth(name='ElevenLabs TTS'),
            'piper': ServiceHealth(name='Piper TTS (local)'),
        }

    # ── Best Service Selection ─────────────────────────────

    def best_stt(self) -> str:
        """Returns the best available STT service name."""
        if self.services['deepgram'].status in (ServiceStatus.HEALTHY, ServiceStatus.UNKNOWN):
            return 'deepgram'
        if self.services['whisper'].status in (ServiceStatus.HEALTHY, ServiceStatus.UNKNOWN):
            return 'whisper'
        # All down — try deepgram anyway (it might recover)
        return 'deepgram'

    def best_llm(self) -> str:
        """Returns the best available LLM service name."""
        if self.services['openclaw'].status in (ServiceStatus.HEALTHY, ServiceStatus.UNKNOWN):
            return 'openclaw'
        if self.services['anthropic'].status in (ServiceStatus.HEALTHY, ServiceStatus.UNKNOWN):
            return 'anthropic'
        if self.services['qwen'].status in (ServiceStatus.HEALTHY, ServiceStatus.UNKNOWN):
            return 'qwen'
        return 'openclaw'  # Try anyway

    def best_tts(self) -> str:
        """Returns the best available TTS service name."""
        if self.services['elevenlabs'].status in (ServiceStatus.HEALTHY, ServiceStatus.UNKNOWN):
            return 'elevenlabs'
        if self.services['piper'].status in (ServiceStatus.HEALTHY, ServiceStatus.UNKNOWN):
            return 'piper'
        return 'elevenlabs'

    def is_fully_local(self) -> bool:
        """True if all active services are local (no internet needed)."""
        return (
            self.best_stt() == 'whisper'
            and self.best_llm() == 'qwen'
            and self.best_tts() == 'piper'
        )

    # ── Health Recording ───────────────────────────────────

    def record_success(self, service: str, latency_ms: float):
        if service in self.services:
            self.services[service].record_success(latency_ms)

    def record_failure(self, service: str):
        if service in self.services:
            self.services[service].record_failure()
            logger.warning(f"⚠️ {service} failure #{self.services[service].failure_count}")

    # ── Background Health Checks ───────────────────────────

    async def start_health_checks(self, interval_seconds: float = 30.0):
        """Start background health check loop."""
        self._health_task = asyncio.create_task(self._health_loop(interval_seconds))

    async def stop_health_checks(self):
        if self._health_task:
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass

    async def _health_loop(self, interval: float):
        """Periodically check all services."""
        while True:
            try:
                await self._check_all()
            except Exception as e:
                logger.error(f"Health check error: {e}")
            await asyncio.sleep(interval)

    async def _check_all(self):
        """Run health checks on all services concurrently."""
        checks = [
            self._check_deepgram(),
            self._check_whisper(),
            self._check_openclaw(),
            self._check_anthropic(),
            self._check_qwen(),
            self._check_elevenlabs(),
            self._check_piper(),
        ]
        await asyncio.gather(*checks, return_exceptions=True)

        # Log summary
        statuses = {k: v.status.value for k, v in self.services.items()}
        logger.debug(f"🏥 Health: {statuses}")

    async def _check_deepgram(self):
        """Ping Deepgram API."""
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as s:
                async with s.get("https://api.deepgram.com/v1/projects", headers={
                    "Authorization": f"Token {self.config.DEEPGRAM_API_KEY}"
                }) as resp:
                    if resp.status in (200, 401):  # 401 = key works but wrong scope — still reachable
                        self.services['deepgram'].record_success(0)
                    else:
                        self.services['deepgram'].record_failure()
        except Exception:
            self.services['deepgram'].record_failure()

    async def _check_whisper(self):
        """Ping whisper.cpp server."""
        try:
            url = getattr(self.config, 'WHISPER_SERVER_URL', 'http://192.168.68.99:8178')
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3)) as s:
                async with s.get(url) as resp:
                    if resp.status == 200:
                        self.services['whisper'].record_success(0)
                    else:
                        self.services['whisper'].record_failure()
        except Exception:
            self.services['whisper'].record_failure()

    async def _check_openclaw(self):
        """Ping OpenClaw gateway."""
        try:
            url = self.config.OPENCLAW_GATEWAY_URL
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as s:
                # Try a lightweight endpoint — exact path depends on OpenClaw's API
                async with s.get(f"{url}/health") as resp:
                    if resp.status == 200:
                        self.services['openclaw'].record_success(0)
                    else:
                        self.services['openclaw'].record_failure()
        except Exception:
            self.services['openclaw'].record_failure()

    async def _check_anthropic(self):
        """Verify Anthropic API key is valid."""
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as s:
                async with s.get("https://api.anthropic.com/v1/messages", headers={
                    "x-api-key": self.config.ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                }) as resp:
                    # 405 = Method Not Allowed (GET on POST endpoint) — means API is reachable
                    if resp.status in (200, 405, 401):
                        self.services['anthropic'].record_success(0)
                    else:
                        self.services['anthropic'].record_failure()
        except Exception:
            self.services['anthropic'].record_failure()

    async def _check_qwen(self):
        """Ping Ollama server on Mac Studio."""
        try:
            url = getattr(self.config, 'OLLAMA_URL', 'http://192.168.68.99:11434')
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3)) as s:
                async with s.get(f"{url}/api/tags") as resp:
                    if resp.status == 200:
                        self.services['qwen'].record_success(0)
                    else:
                        self.services['qwen'].record_failure()
        except Exception:
            self.services['qwen'].record_failure()

    async def _check_elevenlabs(self):
        """Ping ElevenLabs API."""
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as s:
                async with s.get("https://api.elevenlabs.io/v1/user", headers={
                    "xi-api-key": self.config.ELEVENLABS_API_KEY,
                }) as resp:
                    if resp.status == 200:
                        self.services['elevenlabs'].record_success(0)
                    else:
                        self.services['elevenlabs'].record_failure()
        except Exception:
            self.services['elevenlabs'].record_failure()

    async def _check_piper(self):
        """Check if Piper binary exists locally."""
        from tts_local import PiperLocalTTS
        piper = PiperLocalTTS()
        if piper.available:
            self.services['piper'].record_success(0)
        else:
            self.services['piper'].record_failure()

    # ── Status Report ──────────────────────────────────────

    def status_report(self) -> dict:
        """Full health status for monitoring/dashboard."""
        return {
            "stt": {
                "active": self.best_stt(),
                "services": {
                    "deepgram": self.services['deepgram'].to_dict(),
                    "whisper": self.services['whisper'].to_dict(),
                },
            },
            "llm": {
                "active": self.best_llm(),
                "services": {
                    "openclaw": self.services['openclaw'].to_dict(),
                    "anthropic": self.services['anthropic'].to_dict(),
                    "qwen": self.services['qwen'].to_dict(),
                },
            },
            "tts": {
                "active": self.best_tts(),
                "services": {
                    "elevenlabs": self.services['elevenlabs'].to_dict(),
                    "piper": self.services['piper'].to_dict(),
                },
            },
            "fully_local": self.is_fully_local(),
        }
```

### 4.2 Update `server/openclaw_llm.py` — Integrate Fallback Chain

The existing `OpenClawLLMProcessor` already has basic fallback to direct Anthropic. Extend it to use the full fallback chain:

```python
# Update _respond method to use fallback chain:

async def _respond(self, user_text: str):
    """Send user text through best available LLM."""
    await self.push_frame(LLMFullResponseStartFrame())

    best = self._fallback_chain.best_llm() if self._fallback_chain else 'openclaw'
    
    success = False
    t0 = time.monotonic()

    if best == 'openclaw':
        success = await self._respond_via_openclaw(user_text)
        if success:
            self._fallback_chain.record_success('openclaw', (time.monotonic() - t0) * 1000)
        else:
            self._fallback_chain.record_failure('openclaw')
            # Try next in chain
            success = await self._respond_via_anthropic(user_text)
            if not success:
                success = await self._respond_via_qwen(user_text)

    elif best == 'anthropic':
        success = await self._respond_via_anthropic(user_text)
        if not success:
            success = await self._respond_via_qwen(user_text)

    elif best == 'qwen':
        success = await self._respond_via_qwen(user_text)

    if not success:
        await self.push_frame(
            TextFrame(text="Sorry, I'm having trouble thinking right now. All my brain services are down.")
        )

    await self.push_frame(LLMFullResponseEndFrame())


async def _respond_via_qwen(self, user_text: str) -> bool:
    """Local Qwen 30B via Ollama on Mac Studio."""
    try:
        session = await self._get_http_session()
        
        # Ollama chat API
        payload = {
            "model": "qwen3:30b",
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are Buddy, a voice companion. Be brief and conversational. "
                        "1-3 sentences max. No markdown, no formatting. "
                        "Speak naturally — you're being read aloud via TTS."
                    ),
                },
                {"role": "user", "content": user_text},
            ],
            "stream": False,
            "options": {
                "num_predict": 256,  # Keep responses short
            },
        }

        ollama_url = getattr(config, 'OLLAMA_URL', 'http://192.168.68.99:11434')
        async with session.post(
            f"{ollama_url}/api/chat",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                return False
            result = await resp.json()
            text = result.get("message", {}).get("content", "").strip()
            if text:
                await self._emit_sentences(text)
                return True
            return False

    except Exception as e:
        logger.error(f"Qwen fallback error: {e}")
        return False
```

### 4.3 Update `server/config.py`

Add local fallback settings:

```python
# Local fallback services
WHISPER_SERVER_URL = os.getenv("WHISPER_SERVER_URL", "http://192.168.68.99:8178")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://192.168.68.99:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:30b")

PIPER_BINARY = os.getenv("PIPER_BINARY", str(Path.home() / ".local/share/piper/piper/piper"))
PIPER_MODEL = os.getenv("PIPER_MODEL", str(Path.home() / ".local/share/piper/voices/en_US-amy-medium.onnx"))

# Health check interval (seconds)
HEALTH_CHECK_INTERVAL = int(os.getenv("BUDDY_HEALTH_CHECK_INTERVAL", "30"))
```

### 4.4 Update `server/.env`

```ini
# Add local fallback settings:
WHISPER_SERVER_URL=http://192.168.68.99:8178
OLLAMA_URL=http://192.168.68.99:11434
OLLAMA_MODEL=qwen3:30b
# PIPER_BINARY and PIPER_MODEL auto-detect from ~/.local/share/piper
```

---

## PHASE 5: Health Dashboard + Monitoring

### 5.1 Create `server/health.py`

Expose an HTTP health endpoint for monitoring.

```python
"""Health check and monitoring endpoints.

Serves a simple JSON health status at /health and an HTML dashboard at /dashboard.
Runs alongside the Pipecat server (separate aiohttp app on port 7861).
"""

import asyncio
import time

import aiohttp.web
from loguru import logger


class HealthServer:
    """Lightweight HTTP server for health monitoring."""

    def __init__(self, fallback_chain, latency_tracker, port: int = 7861):
        self._chain = fallback_chain
        self._latency = latency_tracker
        self._port = port
        self._start_time = time.time()
        self._runner = None

    async def start(self):
        app = aiohttp.web.Application()
        app.router.add_get('/health', self._handle_health)
        app.router.add_get('/dashboard', self._handle_dashboard)
        app.router.add_get('/metrics', self._handle_metrics)

        self._runner = aiohttp.web.AppRunner(app)
        await self._runner.setup()
        site = aiohttp.web.TCPSite(self._runner, '0.0.0.0', self._port)
        await site.start()
        logger.info(f"📊 Health dashboard: http://0.0.0.0:{self._port}/dashboard")

    async def stop(self):
        if self._runner:
            await self._runner.cleanup()

    async def _handle_health(self, request):
        """JSON health status."""
        status = self._chain.status_report()
        status['uptime_seconds'] = round(time.time() - self._start_time)
        status['avg_latency_ms'] = round(self._latency.average_total_ms, 1)

        # Overall health
        all_healthy = all(
            s.status.value in ('healthy', 'unknown')
            for s in self._chain.services.values()
        )
        status['overall'] = 'healthy' if all_healthy else 'degraded'

        return aiohttp.web.json_response(status)

    async def _handle_metrics(self, request):
        """Prometheus-compatible metrics (optional)."""
        lines = []
        for name, svc in self._chain.services.items():
            lines.append(f'buddy_service_healthy{{service="{name}"}} {1 if svc.status.value == "healthy" else 0}')
            lines.append(f'buddy_service_latency_ms{{service="{name}"}} {svc.avg_latency_ms:.1f}')
            lines.append(f'buddy_service_failures{{service="{name}"}} {svc.failure_count}')

        lines.append(f'buddy_uptime_seconds {time.time() - self._start_time:.0f}')
        lines.append(f'buddy_avg_turn_latency_ms {self._latency.average_total_ms:.1f}')

        return aiohttp.web.Response(
            text='\n'.join(lines) + '\n',
            content_type='text/plain',
        )

    async def _handle_dashboard(self, request):
        """Simple HTML dashboard."""
        status = self._chain.status_report()
        uptime = round(time.time() - self._start_time)
        uptime_str = f"{uptime // 3600}h {(uptime % 3600) // 60}m"

        html = f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8"><title>Buddy Health</title>
<meta http-equiv="refresh" content="10">
<style>
  body {{ font-family: -apple-system, sans-serif; background: #0a0a0a; color: #e0e0e0; padding: 2rem; }}
  h1 {{ color: #4fc3f7; }}
  .card {{ background: #1a1a1a; border-radius: 8px; padding: 1rem; margin: 1rem 0; }}
  .healthy {{ color: #66bb6a; }}
  .degraded {{ color: #ffa726; }}
  .unhealthy {{ color: #ef5350; }}
  .unknown {{ color: #666; }}
  table {{ width: 100%; border-collapse: collapse; }}
  td, th {{ padding: 0.5rem; text-align: left; border-bottom: 1px solid #333; }}
  .badge {{ padding: 2px 8px; border-radius: 4px; font-size: 0.85rem; }}
  .badge.healthy {{ background: #1b5e20; }}
  .badge.degraded {{ background: #e65100; }}
  .badge.unhealthy {{ background: #b71c1c; }}
</style>
</head><body>
<h1>🐾 Buddy Health Dashboard</h1>
<p>Uptime: {uptime_str} | Avg latency: {self._latency.average_total_ms:.0f}ms | Local mode: {'✅' if status['fully_local'] else '❌'}</p>
"""

        for category in ['stt', 'llm', 'tts']:
            cat = status[category]
            html += f'<div class="card"><h3>{category.upper()} — Active: <span class="healthy">{cat["active"]}</span></h3><table>'
            html += '<tr><th>Service</th><th>Status</th><th>Latency</th><th>Failures</th></tr>'
            for name, svc in cat['services'].items():
                badge = svc['status']
                html += f'<tr><td>{svc["name"]}</td><td><span class="badge {badge}">{badge}</span></td>'
                html += f'<td>{svc["avg_latency_ms"]}ms</td><td>{svc["failure_count"]}</td></tr>'
            html += '</table></div>'

        html += '</body></html>'
        return aiohttp.web.Response(text=html, content_type='text/html')
```

### 5.2 Integrate Health Server into `bot.py`

```python
# In bot.py, after pipeline setup:

from fallback import FallbackChain
from health import HealthServer

# Initialize fallback chain
fallback_chain = FallbackChain(config)
await fallback_chain.start_health_checks(interval_seconds=config.HEALTH_CHECK_INTERVAL)

# Pass to LLM processor
openclaw_llm._fallback_chain = fallback_chain

# Start health dashboard
health = HealthServer(fallback_chain, latency_tracker, port=7861)
await health.start()

logger.info("📊 Dashboard: http://0.0.0.0:7861/dashboard")
```

---

## PHASE 6: Process Management (launchd)

### 6.1 Create `scripts/buddy.plist`

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.buddy.voice-server</string>

    <key>ProgramArguments</key>
    <array>
        <!-- Use full path to uv -->
        <string>/Users/elie/.local/bin/uv</string>
        <string>run</string>
        <string>bot.py</string>
    </array>

    <key>WorkingDirectory</key>
    <string>/Users/elie/.openclaw/workspace/projects/buddy/server</string>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>

    <key>ThrottleInterval</key>
    <integer>10</integer>

    <key>StandardOutPath</key>
    <string>/Users/elie/.openclaw/workspace/projects/buddy/logs/buddy.log</string>

    <key>StandardErrorPath</key>
    <string>/Users/elie/.openclaw/workspace/projects/buddy/logs/buddy.err</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/Users/elie/.local/bin:/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
```

### 6.2 Create `scripts/install-service.sh`

```bash
#!/bin/bash
# Install Buddy as a launchd service (auto-start on boot, auto-restart on crash).

set -e

PLIST_SRC="$(dirname "$0")/buddy.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.buddy.voice-server.plist"
LOG_DIR="/Users/elie/.openclaw/workspace/projects/buddy/logs"

# Create log directory
mkdir -p "$LOG_DIR"

# Check uv path
UV_PATH="$(which uv 2>/dev/null || echo '/Users/elie/.local/bin/uv')"
if [ ! -f "$UV_PATH" ]; then
    echo "❌ uv not found at $UV_PATH"
    echo "   Install with: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

# Update plist with correct uv path
sed "s|/Users/elie/.local/bin/uv|$UV_PATH|g" "$PLIST_SRC" > "$PLIST_DST"

echo "📦 Installing Buddy launchd service..."

# Unload if already loaded
launchctl unload "$PLIST_DST" 2>/dev/null || true

# Load
launchctl load "$PLIST_DST"

echo "✅ Buddy service installed and started"
echo ""
echo "Commands:"
echo "  Status:  launchctl list | grep buddy"
echo "  Stop:    launchctl unload $PLIST_DST"
echo "  Start:   launchctl load $PLIST_DST"
echo "  Logs:    tail -f $LOG_DIR/buddy.log"
echo "  Errors:  tail -f $LOG_DIR/buddy.err"
```

### 6.3 Create uninstall script

```bash
#!/bin/bash
# scripts/uninstall-service.sh
PLIST_DST="$HOME/Library/LaunchAgents/com.buddy.voice-server.plist"
launchctl unload "$PLIST_DST" 2>/dev/null || true
rm -f "$PLIST_DST"
echo "✅ Buddy service uninstalled"
```

---

## PHASE 7: Client Quality Indicator

### 7.1 Update `client/web/app.js`

Add a small indicator showing connection quality (cloud vs local mode):

```javascript
// Add to BuddyClient class:

_updateQualityIndicator(mode) {
  const el = document.getElementById('qualityIndicator');
  if (!el) return;

  if (mode === 'cloud') {
    el.textContent = '☁️';
    el.title = 'Cloud mode — best quality';
    el.className = 'quality-indicator cloud';
  } else if (mode === 'local') {
    el.textContent = '🏠';
    el.title = 'Local mode — offline capable';
    el.className = 'quality-indicator local';
  } else if (mode === 'degraded') {
    el.textContent = '⚠️';
    el.title = 'Degraded — some services unavailable';
    el.className = 'quality-indicator degraded';
  }
}
```

### 7.2 Update `client/web/index.html`

Add quality indicator element:
```html
<!-- Inside .app div, top-left corner -->
<div class="quality-indicator" id="qualityIndicator" title="Connection quality">☁️</div>
```

### 7.3 Update `client/web/style.css`

```css
/* ── Quality Indicator ── */
.quality-indicator {
  position: absolute;
  top: 1rem;
  left: 1rem;
  font-size: 1.2rem;
  opacity: 0.5;
  cursor: help;
  transition: opacity 0.3s;
}

.quality-indicator:hover {
  opacity: 1;
}
```

---

## PHASE 8: Final README

### 8.1 Update `README.md`

Replace with comprehensive final README:

```markdown
# 🐾 Buddy — Voice Companion

Always-on voice companion with personality, memory, and tools. Talk to Buddy from any device on your network.

Built with [Pipecat](https://pipecat.ai) + [OpenClaw](https://openclaw.ai).

## Features

- **Voice conversation** — natural back-and-forth, interruption support
- **Wake word** — say "Hey Buddy" (Picovoice Porcupine, on-device)
- **Personality & memory** — powered by OpenClaw (SOUL.md, MEMORY.md)
- **Tool access** — calendar, email, weather, smart home via voice
- **Multi-device** — Mac, iPhone, iPad. One active at a time with clean handoff
- **iPad home station** — always-on ambient display with wake-on-voice
- **Local fallback** — works offline via Whisper.cpp + Qwen 30B + Piper TTS
- **Auto-healing** — health monitoring, service failover, auto-restart

## Architecture

```
Cloud (primary):
  Deepgram STT → Claude via OpenClaw → ElevenLabs TTS

Local fallback:
  Whisper.cpp (Mac Studio) → Qwen 30B (Mac Studio) → Piper TTS (Mac Mini)

Transport: WebRTC (peer-to-peer, LAN)
Orchestration: Pipecat (Python)
```

## Quick Start

### 1. API Keys

| Service | Purpose | Sign Up |
|---------|---------|---------|
| [Deepgram](https://console.deepgram.com) | STT | Free $200 credit |
| [Anthropic](https://console.anthropic.com) | LLM | Pay-as-you-go |
| [ElevenLabs](https://elevenlabs.io) | TTS | Free 10K chars/mo |
| [Picovoice](https://console.picovoice.ai) | Wake word | Free tier |

### 2. Configure

```bash
cd server
cp .env.example .env
# Edit .env with your API keys
```

### 3. Run

```bash
cd server
uv sync
uv run bot.py
```

### 4. Connect

Open `http://localhost:7860/client` in your browser. Click Connect. Talk.

From other devices: `http://<mac-mini-ip>:7860/client`

## iPad Home Station

See [docs/ipad-setup.md](docs/ipad-setup.md) for step-by-step kiosk setup.

## Local Fallback Setup

See [docs/fallback-chain.md](docs/fallback-chain.md) for Whisper.cpp + Piper installation.

## Monitoring

Health dashboard: `http://<mac-mini-ip>:7861/dashboard`

JSON health: `http://<mac-mini-ip>:7861/health`

## Run as Service

```bash
bash scripts/install-service.sh
```

Buddy will auto-start on boot and restart on crash.

## Configuration

See `server/.env` for all options. Key settings:

| Variable | Description | Default |
|----------|-------------|---------|
| `BUDDY_PORT` | Server port | `7860` |
| `BUDDY_LLM_MODEL` | Claude model | `claude-sonnet-4-5-20250929` |
| `ELEVENLABS_VOICE_ID` | TTS voice | Rachel |
| `BUDDY_CONVERSATION_TIMEOUT` | Silence before disconnect | `30` |
| `OLLAMA_URL` | Local LLM server | `http://192.168.68.99:11434` |
| `WHISPER_SERVER_URL` | Local STT server | `http://192.168.68.99:8178` |

## Cost

| Service | Monthly (30 min/day) |
|---------|---------------------|
| Deepgram STT | ~$4 |
| ElevenLabs TTS | ~$9 |
| Claude (via OpenClaw) | existing |
| **Local fallback** | **$0** |
| **Total** | **~$13** |
```

---

## PHASE 9: Validation Checklist

### Fallback Chain
- [ ] Pull ethernet from Mac Mini → conversation continues via local models
- [ ] Response time with local models: <1 second
- [ ] Plug ethernet back in → seamlessly returns to cloud models within 30s
- [ ] Kill Deepgram (wrong API key) → falls back to Whisper.cpp
- [ ] Kill ElevenLabs (wrong API key) → falls back to Piper
- [ ] Kill OpenClaw gateway → falls back to direct Anthropic → then Qwen
- [ ] All three local services down → graceful error message, no crash

### Health & Monitoring
- [ ] `/health` returns JSON with all service statuses
- [ ] `/dashboard` renders HTML with auto-refresh
- [ ] Health checks run every 30s in background
- [ ] Degraded services recover automatically when restored
- [ ] Latency metrics tracked per-turn

### Process Management
- [ ] `launchctl load` starts Buddy
- [ ] `launchctl unload` stops Buddy
- [ ] Kill the Python process → auto-restarts within 10s
- [ ] Reboot Mac Mini → Buddy starts automatically
- [ ] Logs rotate / don't fill disk (check after 24h)

### End-to-End
- [ ] Full cloud path: speak → hear response <1.5s
- [ ] Full local path: speak → hear response <1s
- [ ] iPad always-on: stable for 24h continuous operation
- [ ] Wake word: works from 3m+ distance
- [ ] Multi-device handoff: still works after all the new changes
- [ ] 30-minute conversation: no crashes, no memory leaks

### Quality
- [ ] Piper TTS quality: understandable, natural enough for casual use
- [ ] Qwen responses: coherent, follows voice system prompt (short, conversational)
- [ ] Whisper transcription: accurate for English at conversational volume

---

## PHASE 10: Documentation

### 10.1 Create `docs/fallback-chain.md`

```markdown
# Fallback Chain Architecture

## Overview

Buddy uses a three-tier fallback chain for each pipeline component.
When a cloud service is unavailable or slow, it automatically falls
back to the next tier. When the primary recovers, it switches back.

## STT (Speech-to-Text)

| Tier | Service | Latency | Cost | Quality |
|------|---------|---------|------|---------|
| 1 | Deepgram Nova-2 (streaming) | ~200ms | $0.004/min | Excellent |
| 2 | Whisper.cpp large-v3 (batch) | ~300ms | Free | Excellent |

Tier 2 adds ~100ms latency because it's batch (waits for full utterance)
vs Deepgram's streaming (processes audio in real-time).

## LLM (Language Model)

| Tier | Service | Latency (TTFT) | Cost | Quality |
|------|---------|----------------|------|---------|
| 1 | Claude via OpenClaw | ~500ms | ~$0.003/turn | Best (has tools, memory, personality) |
| 2 | Claude direct | ~500ms | ~$0.003/turn | Good (no tools/memory) |
| 3 | Qwen 30B via Ollama | ~200ms | Free | Good (no tools/memory, shorter context) |

## TTS (Text-to-Speech)

| Tier | Service | Latency | Cost | Quality |
|------|---------|---------|------|---------|
| 1 | ElevenLabs Turbo v2.5 (streaming) | ~500ms | ~$0.01/min | Excellent |
| 2 | Piper (local, batch) | ~50ms | Free | Good |

## Health Monitoring

Background health checks run every 30 seconds. A service is marked
unhealthy after 3 consecutive failures. It's automatically retried
and restored when it recovers.

Dashboard: http://localhost:7861/dashboard
JSON API: http://localhost:7861/health
```

### 10.2 Create `docs/monitoring.md`

```markdown
# Monitoring

## Health Dashboard

Open http://<mac-mini-ip>:7861/dashboard in a browser.
Auto-refreshes every 10 seconds.

Shows:
- Service health for all STT/LLM/TTS services
- Active service for each component
- Average latency per turn
- Uptime
- Whether running in fully-local mode

## JSON Health Endpoint

```
GET http://localhost:7861/health
```

Returns service status, latency, failure counts.

## Prometheus Metrics

```
GET http://localhost:7861/metrics
```

Compatible with Prometheus scraping. Metrics:
- `buddy_service_healthy{service="..."}` — 0 or 1
- `buddy_service_latency_ms{service="..."}` — average response time
- `buddy_service_failures{service="..."}` — consecutive failure count
- `buddy_uptime_seconds` — server uptime
- `buddy_avg_turn_latency_ms` — average voice turn latency

## Logs

```bash
tail -f logs/buddy.log   # stdout
tail -f logs/buddy.err   # stderr
```

Logs include:
- 🎤 User transcriptions
- 💬 Bot responses (sentence-by-sentence)
- ⏱️ Per-turn latency
- 📊 Latency breakdown (STT/LLM/TTS)
- 🏥 Health check results
- ⚠️ Service failures and fallback events
- 📱 Device connect/disconnect events
```

---

## PHASE 11: Git

```bash
cd /Users/elie/.openclaw/workspace/projects/buddy
git add -A
git commit -m "Week 4: Local fallback chain, health monitoring, production polish

- Local STT fallback: whisper.cpp on Mac Studio
- Local TTS fallback: Piper on Mac Mini
- Local LLM fallback: Qwen 30B via Ollama on Mac Studio
- Intelligent fallback chain with auto-failover and recovery
- Health dashboard at :7861/dashboard
- JSON health + Prometheus metrics endpoints
- launchd service (auto-start, auto-restart)
- Quality indicator on client (cloud/local/degraded)
- Install scripts for Piper, whisper.cpp, and launchd service
- Comprehensive README and documentation"
git push origin main
```

---

## KNOWN ISSUES & EDGE CASES

1. **whisper.cpp server mode:** The server binary may be named differently depending on the build. Check `build/bin/` for `server`, `whisper-server`, or `main`. The HTTP API may differ from what's documented — test with `curl` first.

2. **Piper on Apple Silicon:** Piper's macOS ARM64 binary may not be available for all versions. Check releases. If not available, build from source or use the x86 binary under Rosetta 2 (slower but works).

3. **Ollama cold start:** If the Qwen model isn't loaded in memory (nobody used it recently), first inference takes ~15s to load. The cron watchdog script (`scripts/cron-watchdog.sh`) in the main workspace keeps it warm, but if it's cold, the first local response will be slow. Subsequent responses are fast (~200ms TTFT).

4. **Pipecat + batch STT:** Pipecat's pipeline is designed for streaming STT. Integrating batch whisper requires buffering audio frames and emitting a single TranscriptionFrame after VAD end-of-speech. This may require a custom `FrameProcessor` wrapper. Check if Pipecat has a built-in batch STT adapter.

5. **Pipecat + batch TTS:** Similarly, Piper produces a complete audio file, not a stream. You need a custom processor that takes the WAV output, converts to raw PCM frames, and emits them as `AudioRawFrame`s. Check Pipecat's frame types for audio output.

6. **Service health check flapping:** If a service oscillates between healthy and unhealthy (e.g., intermittent network), the 3-failure threshold may cause rapid switching. Consider adding a cooldown: once a service is marked unhealthy, don't recheck for 60s.

7. **Latency measurement accuracy:** The latency tracker measures server-side times. Actual end-to-end latency (user speaks → user hears) includes WebRTC transport (~10ms LAN) and client-side audio buffering (~50-100ms). Real latency is ~100ms higher than measured.

8. **Log rotation:** The launchd plist writes to fixed log files. Over weeks, these can grow large. Add log rotation via `newsyslog` or a cron job that truncates/rotates the files periodically.

---

## AFTER WEEK 4 (Future)

These are ideas for the future, NOT in scope:

- **Voice cloning:** Upload samples to ElevenLabs, create a custom Buddy voice
- **Proactive alerts:** "You have a meeting in 10 minutes" — scheduled via OpenClaw cron
- **Multi-room audio:** Different iPads in different rooms, room-aware responses
- **Raspberry Pi station:** $150 hardware build as alternative to iPad
- **Music/podcast playback:** "Play some jazz" → Spotify/Apple Music integration
- **Camera integration:** "Show me the front door" → display camera feed on iPad
- **Guest mode:** Recognize different voices, adjust personality per person
- **Conversation summarization:** End-of-day summary sent to daily memory file
