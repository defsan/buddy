# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Buddy is an always-on voice companion accessible from iPhone, Mac, and iPad. It uses a **Scenario C hybrid architecture**: cloud services (Deepgram STT, ElevenLabs TTS, Claude LLM) with local fallbacks (Whisper.cpp, Piper TTS, Qwen 30B via Ollama).

## Current State (Week 1 MVP)

Everything runs locally on a single machine for development. Production target is Mac Mini + Mac Studio, but the local-first setup works on any Apple Silicon Mac.

- **STT:** whisper.cpp (local, http://127.0.0.1:8178)
- **LLM:** Claude Sonnet 4.5 via Anthropic API (only paid service)
- **TTS:** Kokoro-82M via Pipecat's built-in `KokoroTTSService` (local, ONNX, ~100MB model auto-downloads to `~/.cache/kokoro-onnx/`)
- **Transport:** WebRTC via Pipecat framework
- **VAD:** Silero VAD

## Common Commands

```bash
# Install dependencies
cd server && uv sync

# Start whisper.cpp server (must be running before bot)
cd ~/whisper.cpp && ./build/bin/whisper-server -m models/ggml-large-v3.bin --host 127.0.0.1 --port 8178

# Run the voice server (in a separate terminal)
cd server && uv run bot.py
# Note: Kokoro model (~100MB) auto-downloads on first run

# Open client in browser
open http://localhost:7860/client

# Setup scripts (run once)
bash scripts/install-whisper.sh   # builds whisper.cpp locally
```

## Architecture

### Pipecat Pipeline (v0.0.103)

Audio frames flow through a processor chain: **Transport → VAD → STT → Context Aggregation → LLM → TTS → PipelineLogger → Transport**. Pipecat uses frame-based processing with built-in interruption handling via `UserStartedSpeakingFrame`/`UserStoppedSpeakingFrame`.

A `PipelineLogger` processor sits after TTS and logs key frame events (LLM TTFB, LLM total time, TTS duration) for observability.

### Key Pipecat API Notes (v0.0.103)

- `LLMContextAggregatorPair.user()` and `.assistant()` are **methods** (not properties)
- VAD analyzer goes on `LLMUserAggregatorParams(vad_analyzer=...)`, NOT on `TransportParams` (deprecated since v0.0.101)
- `allow_interruptions` on `PipelineParams` is deprecated since v0.0.99
- Runner modules (`pipecat.runner.*`) require the `[runner]` extra (fastapi + uvicorn)
- `pipecat.runner.run.main()` handles SmallWebRTCTransport setup and serves the dev client at `/client`

## Deviations from BUILDSPEC-WEEK1

1. **Python version pinned to <3.14** — `onnxruntime` (Silero VAD dependency) has no wheels for Python 3.14 yet. `pyproject.toml` uses `requires-python = ">=3.10,<3.14"`, and `uv sync --python 3.12` is used.

2. **Build backend fixed** — Buildspec had `setuptools.backends._legacy:_Backend` which doesn't exist. Changed to `setuptools.build_meta`.

3. **Added `[runner]` extra** — Buildspec only had `pipecat-ai[webrtc,anthropic,silero]`. The runner (fastapi/uvicorn for serving the WebRTC client) requires `pipecat-ai[webrtc,anthropic,silero,runner]`.

4. **Piper TTS uses Python library, not binary** — The pre-built Piper binary (`piper_macos_aarch64.tar.gz`) is actually an x86_64 binary (mislabeled upstream) and fails on ARM64 without Rosetta. Replaced with `piper-tts` Python package (installed via `uv pip install piper-tts`). The `PiperTTSProcessor` now takes only `model_path` (no `piper_binary`). `config.py` no longer checks for or references a Piper binary.

5. **All services run locally** — Buildspec assumed whisper.cpp on a separate Mac Studio at `192.168.68.99`. All defaults now point to `127.0.0.1` for local-first development. Change `WHISPER_SERVER_URL` in `.env` to point to a remote machine when deploying.

6. **whisper.cpp install script** — Requires `cmake` (`brew install cmake`). The build flag `WHISPER_METAL` is deprecated; future builds should use `GGML_METAL` instead.

7. **Piper install script** — Fixed archive name from `macos_arm64` to `macos_aarch64` to match actual GitHub release asset name. Note: the binary still doesn't run on ARM64 natively; use the Python package instead.

8. **TTS replaced: Piper → Kokoro-82M** — Piper produced robotic speech with unnatural pauses. Replaced with Kokoro-82M (TTS Arena benchmark winner) using Pipecat's built-in `KokoroTTSService` (`pipecat-ai[kokoro]` extra). The ONNX model auto-downloads to `~/.cache/kokoro-onnx/` on first run. Default voice is `af_bella`; configurable via `KOKORO_VOICE` env var. `tts_piper.py` kept as fallback reference.

9. **`pyproject.toml` needs explicit `py-modules`** — setuptools flat-layout auto-discovery fails with multiple top-level `.py` files. Added `[tool.setuptools] py-modules = [...]` to fix the build.

10. **Pipeline observability added** — `bot.py` includes a `PipelineLogger` frame processor that logs LLM TTFB, LLM total response time, and TTS duration. `stt_whisper.py` logs VAD speech start/stop, STT profiling breakdown (audio duration, WAV encode time, whisper HTTP time, total, and RTF).

## Build Specifications

Implementation is organized into 4-week milestones with agent-ready BUILDSPECs:

- **BUILDSPEC-WEEK1.md** — Voice pipeline MVP: Whisper.cpp + Claude + Piper, browser client
- **BUILDSPEC-WEEK2.md** — OpenClaw integration, iOS PWA client, sentence streaming
- **BUILDSPEC-WEEK3.md** — Wake word detection, iPad kiosk mode, multi-device management
- **BUILDSPEC-WEEK4.md** — Local fallback chains, health monitoring, launchd service

Each BUILDSPEC has non-skippable phases (Phase 0–7) with exact file paths, code examples, and verification steps. **Always read the relevant BUILDSPEC before implementing, but check this file's "Deviations" section for corrections.**

## Key Gotchas

- **Pipecat API is under active development** — import paths may vary by version; check docs if imports fail
- **Whisper.cpp binary naming** — the server binary is `whisper-server` in `build/bin/`
- **KokoroTTSService constructor** — uses `voice_id` (not `voice`). Speed is hardcoded at 1.0x in the service. No `speed` constructor param.
- **Kokoro voice options** — `af_bella` (female, default), `af_heart` (female), `am_adam` (male). Set via `KOKORO_VOICE` env var.
- **iPhone requires HTTPS** for microphone access when not on localhost
- **Whisper.cpp is batch-mode** (not streaming) — adds ~300ms latency waiting for speech end
- **iPad echo cancellation** is critical for always-on mode; use `echoCancellation: true` in getUserMedia

## Configuration

All secrets go in `server/.env` (gitignored). See `server/.env.example` for the template. Required keys: `ANTHROPIC_API_KEY`. Optional: `KOKORO_VOICE`, `DEEPGRAM_API_KEY`, `ELEVENLABS_API_KEY`, `PICOVOICE_ACCESS_KEY`.

## Latency Budget

Target end-to-end: **~700ms–1s cloud, ~600–800ms local**. Per-component: STT ~300ms (Whisper.cpp), LLM ~300ms TTFB, TTS ~100ms (Kokoro). Use pipeline logs (`[pipeline]` prefix) and STT profiling logs to measure actual latency.
