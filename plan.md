# Buddy — Voice Companion Project

**Goal:** Always-on voice companion accessible from iPhone, Mac, and a dedicated home device (iPad/Pi). Personality, memory, tools, low latency.

**Owner:** Elie
**Started:** 2026-02-25

---

## Architecture Overview

```
┌──────────┐   ┌──────────┐   ┌──────────┐
│  iPhone   │   │   Mac    │   │  iPad /  │
│  (client) │   │ (client) │   │  Pi Hub  │
└─────┬─────┘   └─────┬────┘   └─────┬────┘
      │               │              │
      └───────┬───────┴──────┬───────┘
              │  WebSocket / WebRTC  │
              ▼                      ▼
        ┌─────────────────────────────┐
        │     Mac Mini (Server)       │
        │                             │
        │  ┌─────────────────────┐    │
        │  │  Voice Orchestrator │    │
        │  │  (Pipecat/Livekit)  │    │
        │  └──┬──────┬──────┬───┘    │
        │     │      │      │        │
        │     ▼      ▼      ▼        │
        │   STT    LLM    TTS        │
        │         (OpenClaw)          │
        │                             │
        └─────────────────────────────┘
```

## Core Stack

### Brain (LLM + Memory)
- OpenClaw (already running on Mac Mini)
- Claude/Grok for cloud, Qwen 30B on Mac Studio for local
- SOUL.md personality, MEMORY.md long-term memory
- Full tool ecosystem (calendar, email, lights, home assistant, etc.)

### Voice Pipeline
```
Mic → STT → LLM → TTS → Speaker
```

| Component | Primary | Fallback |
|-----------|---------|----------|
| STT | Deepgram streaming (~200ms, $0.0043/min) | Whisper.cpp local on Mac Studio |
| LLM | Claude/Grok via OpenClaw | Qwen 30B local |
| TTS | ElevenLabs streaming (~500ms, custom voice) | Piper TTS local |

### Transport
- WebSocket or WebRTC for audio streaming
- Pipecat or Livekit Agents as the orchestration framework

---

## Three Scenarios Evaluated

### Scenario A: OpenAI Realtime API + Thin Clients
- OpenAI handles full voice pipeline server-side
- Mac Mini runs WebSocket relay
- ~300-500ms latency, ~$0.06/min
- **Pros:** Fastest to build, best conversation feel, interruption handling built-in
- **Cons:** Expensive, no local option, limited personality control
- **Build time:** 1-2 weekends

### Scenario B: Fully Local Pipeline on Mac Studio
- Whisper.cpp → Qwen/Claude → Coqui XTTS/Piper
- ~1-1.5s latency local, zero ongoing costs
- **Pros:** Full control, custom voice, private, no API costs
- **Cons:** Higher latency, more setup, lower TTS quality
- **Build time:** 2-4 weekends

### Scenario C: Hybrid (SELECTED) ⭐
- Deepgram STT + Claude via OpenClaw + ElevenLabs TTS
- ~700ms-1s latency, ~$0.01-0.02/min
- Falls back to local when cloud is down
- Integrates with existing OpenClaw tool ecosystem
- **Pros:** Best quality, good latency, existing personality/memory, tool access
- **Cons:** Cloud costs, more integration work
- **Build time:** 2-3 weekends
- **Detailed plan:** See `scenario-c.md`

---

## Clients

### iPhone
- SwiftUI app: audio capture → WebSocket → play response
- Or PWA with Web Audio API (Safari, no App Store needed)
- Widget/shortcut for quick activation

### Mac (Studio/Mini)
- OpenClaw Mac app already has wake-word + push-to-talk
- Could extend, or standalone menu bar app
- Or local web page

### Home Device
**Option 1: iPad on a stand (selected for v1)**
- Old/cheap iPad + charging stand
- PWA or native app connecting to Mac Mini
- Best screen, zero hardware work
- Limitation: less customizable than custom hardware

**Option 2: Raspberry Pi (future)**
- Pi 4/5 + 7" touchscreen + ReSpeaker mic HAT + speaker
- Thin client (Electron/Python) → Mac Mini
- ~$150 total, fully customizable

**Option 3: ESP32-S3-BOX (~$45)**
- Built-in screen, mic, speaker
- Needs firmware work, limited screen

---

## Open Source to Leverage

| Project | What | Use For |
|---------|------|---------|
| **Pipecat** (Daily.co) | Python voice AI framework | Main orchestration framework |
| **Livekit Agents** | WebRTC infra + voice agents | Alternative to Pipecat, better multi-client |
| **Porcupine** (Picovoice) | Wake word detection | "Hey Buddy" activation on clients |
| **Piper TTS** | Local TTS | Fallback when ElevenLabs is down |
| **Whisper.cpp** | Local STT | Fallback when Deepgram is down |
| **Home Assistant Wyoming** | Voice protocol | Integration ideas, Piper/Whisper setup |

---

## Milestones

### Week 1: Voice Pipeline MVP
Get voice in → text → LLM → voice out working end-to-end on Mac.
See `scenario-c.md` for detailed plan.

### Week 2: OpenClaw Integration + iOS Client
Wire personality, memory, and tools. Build mobile client.

### Week 3: Home Device Setup
iPad PWA or Pi station. Wake word detection.

### Week 4: Polish
Interruption handling, VAD tuning, fallback chains, latency optimization.

---

## Cost Estimates

| Service | Rate | Daily Est (30 min use) | Monthly |
|---------|------|----------------------|---------|
| Deepgram STT | $0.0043/min | $0.13 | ~$4 |
| ElevenLabs TTS | ~$0.01/min (Pro plan) | $0.30 | ~$9 |
| Claude API | existing spend | — | — |
| **Total incremental** | | | **~$13/mo** |

Local fallback (Whisper + Piper + Qwen): $0/mo but higher latency.

---

## Open Questions
- [ ] Pipecat vs Livekit — need to evaluate both
- [ ] ElevenLabs voice: clone custom voice or use preset?
- [ ] Wake word: "Hey Buddy"? Custom via Porcupine?
- [ ] iPad PWA vs native app — PWA is faster to build, native has better audio APIs
- [ ] How to handle interruptions (user talks while TTS is playing)
