# Scenario C: Hybrid Voice Pipeline — Detailed Plan

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    CLIENT (iPhone/iPad/Mac)                  │
│                                                             │
│  ┌──────────┐    ┌──────────┐    ┌──────────────────────┐   │
│  │   Mic    │───▶│   VAD    │───▶│  WebSocket/WebRTC    │   │
│  └──────────┘    │(silero)  │    │  Audio Stream Out     │   │
│                  └──────────┘    └──────────┬────────────┘   │
│                                             │                │
│  ┌──────────┐    ┌──────────────────────┐   │                │
│  │ Speaker  │◀───│  Audio Stream In      │◀──┘                │
│  └──────────┘    └──────────────────────┘                    │
│                                                             │
│  ┌──────────────┐                                           │
│  │ Wake Word    │  (Porcupine — "Hey Buddy")                │
│  │ (optional)   │  Activates mic capture                    │
│  └──────────────┘                                           │
└─────────────────────────────────────────────────────────────┘
                              │
                    WebSocket / WebRTC
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                  MAC MINI (Server)                           │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐    │
│  │              PIPECAT PIPELINE                        │    │
│  │                                                     │    │
│  │  Audio In ──▶ VAD ──▶ STT ──▶ LLM ──▶ TTS ──▶ Out  │    │
│  │              │        │       │        │             │    │
│  │           Silero   Deepgram  OpenClaw  ElevenLabs    │    │
│  │           VAD      Streaming  API     Streaming      │    │
│  │                       │       │                      │    │
│  │                    Whisper  Qwen 30B                  │    │
│  │                   (fallback) (fallback)               │    │
│  └─────────────────────────────────────────────────────┘    │
│                              │                              │
│                              ▼                              │
│  ┌─────────────────────────────────────────────────────┐    │
│  │              OPENCLAW GATEWAY                        │    │
│  │                                                     │    │
│  │  SOUL.md ──── Personality                           │    │
│  │  MEMORY.md ── Long-term memory                      │    │
│  │  Tools ────── Calendar, email, lights, HA, etc.     │    │
│  │  Sessions ─── Conversation history                  │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

## Component Breakdown

### 1. Voice Activity Detection (VAD)
- **Silero VAD** — lightweight neural VAD, runs on-device or server
- Detects when user starts/stops speaking
- Prevents STT from processing silence (saves cost + latency)
- Pipecat has built-in Silero VAD support

### 2. Speech-to-Text (STT)
**Primary: Deepgram Nova-2 Streaming**
- WebSocket streaming — sends audio chunks, gets words back in real-time
- ~200ms latency for first word
- $0.0043/min ($0.0036/min with Nova-2, pay-as-you-go)
- Interim results (show partial transcription while speaking)
- Endpointing: auto-detects end of utterance

**Fallback: Whisper.cpp on Mac Studio**
- whisper-large-v3 runs at ~10x realtime on M4 Ultra
- ~300ms for short utterances
- Zero cost, fully private
- Slightly less accurate than Deepgram for streaming

### 3. LLM (Brain)
**Primary: Claude via OpenClaw API**
- Use OpenClaw's existing session/message infrastructure
- Personality from SOUL.md, memory from MEMORY.md
- Tool access (calendar, email, home automation, etc.)
- System prompt tuned for voice: "Be concise. Respond in 1-3 sentences unless asked for detail."

**Fallback: Qwen 30B on Mac Studio**
- 100 tok/s, good enough for voice responses
- Ollama API at 192.168.68.99:11434
- No cost, works offline

**Voice-specific LLM considerations:**
- Shorter responses (voice ≠ text — nobody wants a 500-word spoken answer)
- Conversation mode: more back-and-forth, less monologue
- Emotion/tone hints in system prompt
- Function calling for actions ("turn off the lights", "set a timer")

### 4. Text-to-Speech (TTS)
**Primary: ElevenLabs Streaming**
- Turbo v2.5 model — ~500ms to first audio chunk
- Streaming: starts playing before full response is generated
- Voice cloning: upload samples → custom voice
- Pricing: ~$0.01/min on Pro plan ($22/mo for 100K chars)
- WebSocket API for lowest latency

**Fallback: Piper TTS (local)**
- Runs on Mac Mini, ~50ms latency
- Multiple voices available, decent quality
- No cost, works offline
- Quality gap vs ElevenLabs is noticeable but acceptable

### 5. Transport Layer
**Option A: WebSocket (simpler)**
- Raw audio frames over WebSocket
- Client captures PCM audio, sends chunks
- Server sends back PCM/opus audio chunks
- Easier to implement, works everywhere
- ~50-100ms transport overhead on LAN

**Option B: WebRTC via Livekit (better for multi-client)**
- Built-in echo cancellation, noise suppression
- Handles NAT traversal for remote access
- Better for iPad/iPhone clients
- Livekit server runs on Mac Mini
- More setup but production-grade

**Recommendation:** Start with WebSocket for Week 1, migrate to Livekit later if needed.

### 6. Orchestration: Pipecat
Pipecat is a Python framework by Daily.co specifically for building voice AI agents.

```python
# Simplified Pipecat pipeline
pipeline = Pipeline([
    transport.input(),          # Audio from client
    silero_vad,                 # Voice activity detection
    deepgram_stt,              # Speech-to-text (streaming)
    openclaw_llm,              # LLM via OpenClaw API
    elevenlabs_tts,            # Text-to-speech (streaming)
    transport.output(),         # Audio back to client
])
```

**Why Pipecat:**
- Handles the full pipeline orchestration
- Built-in support for Deepgram, ElevenLabs, OpenAI, Anthropic
- VAD, interruption handling, turn-taking
- Frame-based architecture (audio frames flow through processors)
- Active development, good docs
- Python — easy to extend with custom processors (like an OpenClaw integration)

## Latency Budget

```
User speaks          0ms
VAD detects speech   ~100ms
Audio to server      ~10ms (LAN)
STT streaming        ~200ms (first words)
STT endpointing      ~300ms (detects end of speech)
LLM first token      ~500ms (Claude) / ~200ms (Qwen local)  
TTS first chunk      ~500ms (ElevenLabs) / ~50ms (Piper)
Audio to client      ~10ms (LAN)
                    ─────────
Total to first audio: ~1.0-1.2s (cloud) / ~0.6-0.8s (local)
```

With streaming TTS, the user hears audio starting ~1s after they stop speaking. This feels natural — similar to a person thinking before responding.

## Interruption Handling

Critical for natural conversation. When user starts talking while TTS is playing:

1. Client detects user speech via VAD
2. Immediately stops TTS playback
3. Sends interrupt signal to server
4. Server cancels in-flight TTS generation
5. Server processes new user input
6. Pipeline restarts with new utterance

Pipecat handles this natively with its `UserStartedSpeakingFrame` / `UserStoppedSpeakingFrame` system.

## OpenClaw Integration Strategy

Two approaches:

**Approach A: HTTP API (simpler)**
- Pipecat sends transcribed text to OpenClaw via HTTP/WebSocket
- OpenClaw processes as a regular message, returns response text
- Pipecat sends response text to TTS
- Existing sessions, memory, tools all work

**Approach B: Custom Pipecat Processor (tighter)**
- Build a `OpenClawLLMProcessor` that speaks OpenClaw's internal protocol
- Direct WebSocket connection to gateway
- Can stream tokens for faster TTS start
- More work but lower latency

**Recommendation:** Start with Approach A. The latency difference is small (~100ms) and it's much simpler.

## File Structure

```
projects/buddy/
├── plan.md                 # This high-level plan
├── scenario-c.md           # This file
├── server/
│   ├── requirements.txt    # Python deps
│   ├── buddy_server.py     # Main Pipecat server
│   ├── openclaw_llm.py     # OpenClaw LLM processor
│   ├── config.py           # API keys, settings
│   └── fallback.py         # Local fallback chain
├── client/
│   ├── web/                # PWA client (iPad/Mac)
│   │   ├── index.html
│   │   ├── app.js
│   │   └── audio.js
│   └── ios/                # Native iOS (future)
└── docs/
    ├── setup.md
    └── voice-tuning.md
```

## Security

- Server only listens on LAN (Tailscale for remote)
- API keys stored in config, not in code
- Audio is not stored by default (option to enable for debugging)
- ElevenLabs/Deepgram data policies: audio processed but not retained on free/paid plans

## Scaling Notes

- Mac Mini handles 1-2 concurrent voice sessions easily
- Mac Studio can handle 4-6+ with local models
- Livekit can distribute clients across rooms if needed
- Each additional client adds ~negligible server load (audio is lightweight)
