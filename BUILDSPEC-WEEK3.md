# BUILDSPEC — Week 3: Home Device + Wake Word Detection

> **Audience:** AI coding agent (Claude Code, Cursor, Copilot, etc.)
> **Prerequisite:** Week 2 complete — Pipecat voice pipeline with OpenClaw integration and PWA client working.
> **Do not skip steps.** Each phase builds on the previous one. Verify each phase works before moving to the next.
> **Repo:** `git@github.com:defsan/buddy.git`
> **Working directory:** `/Users/elie/.openclaw/workspace/projects/buddy`
> **Host machine:** Mac Mini M4 (16GB), macOS, Python 3.12+, `uv` package manager
> **Secondary machine:** Mac Studio Ultra M4 (128GB) at `192.168.68.99` — Ollama host

---

## GOAL

Add always-on "Hey Buddy" wake word and turn an iPad into a dedicated home companion station:

1. "Hey Buddy" wake word runs on-device via Picovoice Porcupine (WASM, zero server cost)
2. iPad always-on kiosk: ambient display, auto-reconnect, wake-on-voice
3. Multi-device session management: only one device active at a time, clean handoff
4. Audio pipeline hardening: echo cancellation, VAD tuning for always-on, noise floor calibration
5. UX polish: audio feedback chimes, tool-use indicators, conversation flow improvements
6. Security: LAN-only, no key exposure, optional Tailscale for remote

**Success metric:** iPad on a stand, always on. Say "Hey Buddy, what's on my calendar?" from across the room — hear a real answer within 2.5 seconds. Open iPhone PWA mid-conversation → iPad yields automatically.

---

## FILE STRUCTURE (additions to Week 2)

```
projects/buddy/
├── server/
│   ├── bot.py                  # Updated: conversation end detection, data channel events
│   ├── openclaw_llm.py         # Updated: tool-use event forwarding
│   ├── device_manager.py       # NEW: multi-device session management
│   ├── config.py               # Updated: add Picovoice, VAD tuning, debug settings
│   ├── pyproject.toml          # Unchanged from Week 2
│   └── .env                    # Updated: add PICOVOICE_ACCESS_KEY
├── client/
│   └── web/
│       ├── index.html          # Updated: ambient mode, wake word UI states
│       ├── app.js              # Updated: wake word integration, always-on, data channel
│       ├── wakeword.js         # NEW: Porcupine wake word detector
│       ├── always-on.js        # NEW: auto-reconnect, wake lock, ambient mode
│       ├── sounds.js           # NEW: procedural audio feedback (Web Audio API)
│       ├── style.css           # Updated: ambient mode, clock, tool indicator
│       ├── manifest.json       # Unchanged
│       ├── sw.js               # Unchanged
│       ├── models/             # NEW: Porcupine wake word model files
│       │   └── hey-buddy_en_wasm.ppn   # Trained via Picovoice Console
│       └── icons/
│           ├── icon-192.png
│           └── icon-512.png
├── docs/
│   └── ipad-setup.md          # NEW: step-by-step iPad kiosk setup guide
└── README.md                   # Updated with Week 3 features
```

---

## PHASE 0: Prerequisites (Manual — Elie Must Do)

These cannot be automated. The coding agent should check for them and print clear instructions if missing.

### 0.1 Picovoice Account + Access Key

1. Sign up at https://console.picovoice.ai
2. Copy the **Access Key** from the dashboard
3. Add to `server/.env`:
   ```
   PICOVOICE_ACCESS_KEY=your_access_key_here
   ```

### 0.2 Train "Hey Buddy" Wake Word

1. In Picovoice Console → Porcupine → Train Keyword
2. Type: `Hey Buddy`
3. Select platform: **Web (WASM)**
4. Download the `.ppn` model file
5. Save to: `client/web/models/hey-buddy_en_wasm.ppn`

**If Elie has not done this yet:** The client should work without wake word (falls back to push-to-talk mode). Print a clear message in the browser console: `"Wake word disabled — no .ppn model found. Using push-to-talk mode."`

### 0.3 iPad

Any iPad running iPadOS 15+ with Safari. Doesn't need to be new — an old iPad on a cheap stand works fine.

---

## PHASE 1: Wake Word Detection (Client-Side)

### 1.1 Install Porcupine Web SDK

The Porcupine WASM library is loaded client-side. There are two approaches:

**Option A: CDN (simpler, recommended for v1)**
```html
<!-- In index.html <head> -->
<script src="https://unpkg.com/@picovoice/porcupine-web@3/dist/iife/index.js"></script>
```

**Option B: npm + bundler**
If the project uses a bundler (Vite, esbuild), install via npm:
```bash
npm install @picovoice/porcupine-web
```

**Recommended:** Option A for now. No build step needed.

### 1.2 Create `client/web/wakeword.js`

```javascript
/**
 * Wake Word Detection via Picovoice Porcupine.
 *
 * Runs entirely on-device (WASM). Listens for "Hey Buddy" and
 * fires a callback. Uses ~2% CPU continuously.
 *
 * Usage:
 *   const ww = new WakeWordDetector(accessKey, () => { startConversation(); });
 *   await ww.start();
 *   // Later:
 *   ww.pause();   // During active conversation (don't detect wake word while talking)
 *   ww.resume();  // After conversation ends
 *   ww.stop();    // Cleanup
 */

class WakeWordDetector {
  constructor(accessKey, onDetected) {
    this.accessKey = accessKey;
    this.onDetected = onDetected;
    this.porcupine = null;
    this.mediaStream = null;
    this.audioContext = null;
    this.processorNode = null;
    this.isRunning = false;
    this.isPaused = false;
  }

  async start() {
    if (this.isRunning) return;

    try {
      // Check if model file exists
      const modelCheck = await fetch('models/hey-buddy_en_wasm.ppn', { method: 'HEAD' });
      if (!modelCheck.ok) {
        console.warn('⚠️ Wake word model not found at models/hey-buddy_en_wasm.ppn');
        console.warn('   Wake word disabled — using push-to-talk mode.');
        console.warn('   To enable: train "Hey Buddy" at https://console.picovoice.ai');
        return false;
      }

      // Initialize Porcupine
      // The exact API depends on the Porcupine Web SDK version.
      // v3.x uses PorcupineWorker.create() or Porcupine.create()
      // Check the global namespace after loading the CDN script.
      const PorcupineModule = window.Porcupine || window.PorcupineWorker;
      if (!PorcupineModule) {
        console.error('Porcupine SDK not loaded');
        return false;
      }

      this.porcupine = await PorcupineModule.create(
        this.accessKey,
        [{
          publicPath: 'models/hey-buddy_en_wasm.ppn',
          label: 'hey_buddy',
        }]
      );

      // Get microphone stream
      this.mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        }
      });

      // Create audio processing pipeline
      this.audioContext = new AudioContext({ sampleRate: this.porcupine.sampleRate || 16000 });
      const source = this.audioContext.createMediaStreamSource(this.mediaStream);

      // Process audio frames through Porcupine
      // Porcupine expects 512-sample frames at 16kHz
      const frameLength = this.porcupine.frameLength || 512;
      this.processorNode = this.audioContext.createScriptProcessor(frameLength, 1, 1);

      let buffer = new Int16Array(0);

      this.processorNode.onaudioprocess = (event) => {
        if (this.isPaused) return;

        const inputData = event.inputBuffer.getChannelData(0);
        // Convert Float32 to Int16
        const int16 = new Int16Array(inputData.length);
        for (let i = 0; i < inputData.length; i++) {
          int16[i] = Math.max(-32768, Math.min(32767, Math.round(inputData[i] * 32768)));
        }

        // Accumulate buffer
        const newBuffer = new Int16Array(buffer.length + int16.length);
        newBuffer.set(buffer);
        newBuffer.set(int16, buffer.length);
        buffer = newBuffer;

        // Process complete frames
        while (buffer.length >= frameLength) {
          const frame = buffer.slice(0, frameLength);
          buffer = buffer.slice(frameLength);

          const keywordIndex = this.porcupine.process(frame);
          if (keywordIndex >= 0) {
            console.log('🎤 Wake word detected: "Hey Buddy"');
            this.onDetected();
          }
        }
      };

      source.connect(this.processorNode);
      this.processorNode.connect(this.audioContext.destination);

      this.isRunning = true;
      console.log('👂 Wake word detection active — say "Hey Buddy"');
      return true;

    } catch (err) {
      console.error('Wake word init failed:', err);
      return false;
    }
  }

  pause() {
    this.isPaused = true;
  }

  resume() {
    this.isPaused = false;
  }

  stop() {
    this.isPaused = false;
    this.isRunning = false;
    if (this.processorNode) {
      this.processorNode.disconnect();
      this.processorNode = null;
    }
    if (this.audioContext) {
      this.audioContext.close();
      this.audioContext = null;
    }
    if (this.mediaStream) {
      this.mediaStream.getTracks().forEach(t => t.stop());
      this.mediaStream = null;
    }
    if (this.porcupine) {
      this.porcupine.delete();
      this.porcupine = null;
    }
  }
}
```

**Implementation notes:**

1. The Porcupine Web SDK API varies by version. Check the actual installed/loaded version's API. Common patterns:
   - v2.x: `Porcupine.create(accessKey, keywords, detectionCallback)`
   - v3.x: `PorcupineWorker.create(accessKey, keywords)` then `porcupine.process(frame)` returns keyword index
   - Some versions use a Worker-based approach where detection fires via callback

2. `ScriptProcessorNode` is deprecated but universally supported. For production, migrate to `AudioWorkletNode`. For v1, ScriptProcessorNode is fine.

3. The mic stream obtained here is separate from the WebRTC mic stream used during conversation. When wake word triggers → pause wake word → start WebRTC conversation → on conversation end → stop WebRTC → resume wake word. Do NOT share the same stream.

4. If the Picovoice Access Key is missing, the constructor will throw. Handle this gracefully — fall back to push-to-talk.

### 1.3 Create `client/web/sounds.js`

Procedural audio feedback using Web Audio API. No external audio files needed.

```javascript
/**
 * Procedural audio feedback sounds.
 *
 * All sounds are generated via Web Audio API — no files to load.
 * Keep sounds short and subtle. These are UI feedback, not music.
 */

class SoundEffects {
  constructor() {
    this.ctx = null;
    this.enabled = true;
  }

  _ensureContext() {
    if (!this.ctx || this.ctx.state === 'closed') {
      this.ctx = new AudioContext();
    }
    if (this.ctx.state === 'suspended') {
      this.ctx.resume();
    }
    return this.ctx;
  }

  /** Rising two-tone chime — wake word acknowledged */
  wakeWordAck() {
    if (!this.enabled) return;
    const ctx = this._ensureContext();
    const now = ctx.currentTime;

    // First tone
    this._tone(ctx, 660, 0.08, now, 0.08);
    // Second tone (higher)
    this._tone(ctx, 880, 0.08, now + 0.1, 0.12);
  }

  /** Soft ding — connected */
  connected() {
    if (!this.enabled) return;
    const ctx = this._ensureContext();
    this._tone(ctx, 784, 0.06, ctx.currentTime, 0.15);
  }

  /** Descending tone — disconnected */
  disconnected() {
    if (!this.enabled) return;
    const ctx = this._ensureContext();
    const now = ctx.currentTime;
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.frequency.setValueAtTime(600, now);
    osc.frequency.exponentialRampToValueAtTime(300, now + 0.2);
    gain.gain.setValueAtTime(0.06, now);
    gain.gain.exponentialRampToValueAtTime(0.001, now + 0.2);
    osc.start(now);
    osc.stop(now + 0.2);
  }

  /** Low buzz — error */
  error() {
    if (!this.enabled) return;
    const ctx = this._ensureContext();
    const now = ctx.currentTime;
    this._tone(ctx, 220, 0.05, now, 0.25);
  }

  /** Subtle tick — tool executing */
  toolStart() {
    if (!this.enabled) return;
    const ctx = this._ensureContext();
    this._tone(ctx, 1200, 0.03, ctx.currentTime, 0.05);
  }

  _tone(ctx, freq, volume, startTime, duration) {
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.frequency.value = freq;
    osc.type = 'sine';
    gain.gain.setValueAtTime(volume, startTime);
    gain.gain.exponentialRampToValueAtTime(0.001, startTime + duration);
    osc.start(startTime);
    osc.stop(startTime + duration);
  }
}
```

### 1.4 Create `client/web/always-on.js`

```javascript
/**
 * Always-On Manager for iPad kiosk mode.
 *
 * Features:
 * - Auto-reconnect with exponential backoff
 * - Screen Wake Lock (prevents sleep)
 * - Ambient display mode (dim after idle timeout)
 * - Clock display in ambient mode
 */

class AlwaysOnManager {
  constructor(buddyClient) {
    this.client = buddyClient;
    this.reconnectDelay = 1000;
    this.maxReconnectDelay = 30000;
    this.wakeLock = null;
    this.idleTimer = null;
    this.isAmbient = false;
    this.ambientTimeoutMs = 120_000; // 2 minutes
    this.clockInterval = null;
  }

  /** Enable all always-on features */
  async enable() {
    this._enableAutoReconnect();
    await this._enableWakeLock();
    this._enableAmbientMode();
    this._startClock();
    console.log('♾️ Always-on mode enabled');
  }

  disable() {
    this._releaseWakeLock();
    this._stopClock();
    if (this.idleTimer) clearTimeout(this.idleTimer);
  }

  /** Call this when voice activity is detected (wakes from ambient) */
  onActivity() {
    this._resetIdleTimer();
  }

  // ── Auto-Reconnect ──────────────────────────────────────

  _enableAutoReconnect() {
    // Hook into client disconnect events
    const originalDisconnect = this.client._onDisconnected?.bind(this.client);

    this.client._onDisconnected = async () => {
      if (originalDisconnect) originalDisconnect();

      // Don't auto-reconnect if user manually disconnected
      if (this.client._userDisconnected) return;

      console.log(`🔄 Reconnecting in ${this.reconnectDelay}ms...`);
      await this._sleep(this.reconnectDelay);
      this.reconnectDelay = Math.min(this.reconnectDelay * 2, this.maxReconnectDelay);

      try {
        await this.client._connect();
        this.reconnectDelay = 1000; // Reset on success
      } catch (e) {
        console.warn('Reconnect failed, retrying...');
        // Will trigger _onDisconnected again → retry loop
      }
    };
  }

  // ── Wake Lock ───────────────────────────────────────────

  async _enableWakeLock() {
    if (!('wakeLock' in navigator)) {
      console.warn('Wake Lock API not available');
      return;
    }

    try {
      this.wakeLock = await navigator.wakeLock.request('screen');
      console.log('🔒 Screen wake lock acquired');

      // Re-acquire on visibility change (Safari releases it when tab is backgrounded)
      document.addEventListener('visibilitychange', async () => {
        if (document.visibilityState === 'visible' && !this.wakeLock) {
          try {
            this.wakeLock = await navigator.wakeLock.request('screen');
          } catch (e) { /* ignore */ }
        }
      });

      this.wakeLock.addEventListener('release', () => {
        this.wakeLock = null;
      });
    } catch (e) {
      console.warn('Wake Lock request failed:', e);
    }
  }

  _releaseWakeLock() {
    if (this.wakeLock) {
      this.wakeLock.release();
      this.wakeLock = null;
    }
  }

  // ── Ambient Mode ────────────────────────────────────────

  _enableAmbientMode() {
    const events = ['touchstart', 'mousemove', 'keydown', 'click'];
    events.forEach(e => document.addEventListener(e, () => this._resetIdleTimer()));
    this._resetIdleTimer();
  }

  _resetIdleTimer() {
    if (this.idleTimer) clearTimeout(this.idleTimer);
    if (this.isAmbient) this._wakeFromAmbient();
    this.idleTimer = setTimeout(() => this._enterAmbient(), this.ambientTimeoutMs);
  }

  _enterAmbient() {
    this.isAmbient = true;
    document.body.classList.add('ambient');
    console.log('😴 Entering ambient mode');
  }

  _wakeFromAmbient() {
    this.isAmbient = false;
    document.body.classList.remove('ambient');
    console.log('👀 Waking from ambient mode');
  }

  // ── Clock ───────────────────────────────────────────────

  _startClock() {
    const clockEl = document.getElementById('ambientClock');
    if (!clockEl) return;

    this.clockInterval = setInterval(() => {
      const now = new Date();
      clockEl.textContent = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }, 1000);
  }

  _stopClock() {
    if (this.clockInterval) {
      clearInterval(this.clockInterval);
      this.clockInterval = null;
    }
  }

  _sleep(ms) {
    return new Promise(r => setTimeout(r, ms));
  }
}
```

### 1.5 Update `client/web/index.html`

Add the new scripts and ambient clock element. Add these before the closing `</body>`:

```html
<!-- Add ambient clock element inside .app div, after .connect-btn -->
<div class="ambient-clock" id="ambientClock"></div>

<!-- Add tool indicator element inside .app div -->
<div class="tool-indicator" id="toolIndicator"></div>

<!-- Scripts (order matters) -->
<script src="https://unpkg.com/@picovoice/porcupine-web@3/dist/iife/index.js"></script>
<script src="sounds.js"></script>
<script src="wakeword.js"></script>
<script src="always-on.js"></script>
<script src="app.js"></script>
```

### 1.6 Update `client/web/app.js`

Major update — integrate wake word, always-on, sounds, and data channel.

The key state machine change:

```
PREVIOUS (Week 2):
  DISCONNECTED → [tap Connect] → CONNECTED → LISTENING/THINKING/SPEAKING

NEW (Week 3):
  WAKE_WORD_LISTENING → [say "Hey Buddy"] → CONNECTING → CONNECTED →
    LISTENING/THINKING/SPEAKING → [conversation ends] → WAKE_WORD_LISTENING

  Also: DISCONNECTED → [tap Connect] → CONNECTED (push-to-talk still works)
```

Add these states and behaviors to the existing `BuddyClient` class:

```javascript
// New states to add:
const STATE = {
  DISCONNECTED: 'disconnected',
  WAKE_LISTENING: 'wake_listening',  // NEW — wake word active, waiting
  CONNECTING: 'connecting',
  CONNECTED: 'connected',
  LISTENING: 'listening',
  THINKING: 'thinking',
  SPEAKING: 'speaking',
};

// In constructor, add:
this.sounds = new SoundEffects();
this.wakeWord = null;
this.alwaysOn = null;
this.dataChannel = null;
this.conversationTimeout = null;
this.conversationTimeoutMs = 30_000; // 30s silence → end conversation

// New method: initialize always-on mode (call after first user interaction)
async enableAlwaysOn(picovoiceAccessKey) {
  // Start wake word
  if (picovoiceAccessKey) {
    this.wakeWord = new WakeWordDetector(picovoiceAccessKey, () => {
      this.sounds.wakeWordAck();
      this._onWakeWord();
    });
    const started = await this.wakeWord.start();
    if (started) {
      this._setState(STATE.WAKE_LISTENING);
    }
  }

  // Start always-on manager
  this.alwaysOn = new AlwaysOnManager(this);
  await this.alwaysOn.enable();
}

// Wake word triggered
async _onWakeWord() {
  if (this.wakeWord) this.wakeWord.pause();
  await this._connect();
}

// Override _connect to set up data channel
// After RTCPeerConnection is created, add:
this.dataChannel = this.pc.createDataChannel('control');
this.dataChannel.onmessage = (event) => {
  const msg = JSON.parse(event.data);
  this._handleServerMessage(msg);
};

// Handle server-side control messages
_handleServerMessage(msg) {
  switch (msg.type) {
    case 'state':
      // Server indicates pipeline state
      if (msg.state === 'listening') this._setState(STATE.LISTENING);
      if (msg.state === 'thinking') this._setState(STATE.THINKING);
      if (msg.state === 'speaking') this._setState(STATE.SPEAKING);
      break;
    case 'tool_use':
      // Show tool indicator
      this._showToolIndicator(msg.tool, msg.status);
      break;
    case 'conversation_end':
      // Server says conversation is over
      this._endConversation();
      break;
    case 'yield':
      // Another device connected — yield gracefully
      this._endConversation('Another device is active');
      break;
  }
  // Any server message counts as activity
  if (this.alwaysOn) this.alwaysOn.onActivity();
}

// End conversation and return to wake word listening
_endConversation(reason) {
  if (this.conversationTimeout) clearTimeout(this.conversationTimeout);
  this._disconnect();
  if (this.wakeWord) {
    this.wakeWord.resume();
    this._setState(STATE.WAKE_LISTENING);
  } else {
    this._setState(STATE.DISCONNECTED);
  }
  if (reason) {
    this.status.textContent = reason;
    setTimeout(() => {
      if (this.state === STATE.WAKE_LISTENING) {
        this.status.textContent = 'say "Hey Buddy"';
      }
    }, 3000);
  }
}

// Tool indicator
_showToolIndicator(tool, status) {
  const el = document.getElementById('toolIndicator');
  if (!el) return;
  const icons = { calendar: '🗓', weather: '🌤', email: '📧', lights: '💡', search: '🔍' };
  const icon = icons[tool] || '🔧';
  if (status === 'running') {
    el.textContent = `${icon} Working on it...`;
    el.classList.add('visible');
    this.sounds.toolStart();
  } else {
    el.classList.remove('visible');
  }
}

// Update _setState for new states
// Add to the state→orb class map:
//   [STATE.WAKE_LISTENING]: 'idle',
// Add to the state→status text map:
//   [STATE.WAKE_LISTENING]: 'say "Hey Buddy"',
// Add to the button logic:
//   if WAKE_LISTENING: button says "Push to Talk" (manual override)
```

**IMPORTANT:** These are modifications to the existing `app.js` from Week 2. Do NOT rewrite the file from scratch — merge these additions into the existing WebRTC connection logic. Keep all existing functionality working.

### 1.7 Update `client/web/style.css`

Add ambient mode and tool indicator styles:

```css
/* ── Ambient Mode ── */
body.ambient .app {
  opacity: 0.3;
  transition: opacity 1s ease;
}

body.ambient .orb {
  animation: pulse 6s ease-in-out infinite !important;
}

body.ambient .connect-btn,
body.ambient .settings-btn,
body.ambient .status {
  opacity: 0;
  transition: opacity 1s ease;
}

.ambient-clock {
  display: none;
  font-size: 4rem;
  font-weight: 200;
  color: var(--text-dim);
  position: absolute;
  bottom: 20%;
  text-align: center;
  width: 100%;
}

body.ambient .ambient-clock {
  display: block;
}

/* ── Tool Indicator ── */
.tool-indicator {
  position: absolute;
  top: 50%;
  transform: translateY(80px);
  font-size: 0.9rem;
  color: var(--accent);
  opacity: 0;
  transition: opacity 0.3s ease;
}

.tool-indicator.visible {
  opacity: 1;
}
```

### 1.8 Update `server/.env`

```ini
# Add:
PICOVOICE_ACCESS_KEY=your_picovoice_access_key_here

# Conversation timeout (seconds of silence before auto-disconnect)
BUDDY_CONVERSATION_TIMEOUT=30

# Debug: log transcripts to file
BUDDY_DEBUG_TRANSCRIPTS=false
```

### 1.9 Verify wake word

1. Ensure `models/hey-buddy_en_wasm.ppn` exists in `client/web/models/`
2. Ensure `PICOVOICE_ACCESS_KEY` is set in `.env` (and exposed to client — see note below)
3. Open browser client
4. Say "Hey Buddy" — should hear chime, then connect to server

**Access key delivery to client:** The Picovoice Access Key needs to be available client-side. Options:
- **Option A:** Hardcode in a `client/web/config.js` file (gitignored). Simple, fine for LAN-only.
- **Option B:** Serve it from a `/api/config` endpoint on the server. Slightly more secure.
- **Recommended:** Option A for v1. Create `client/web/config.js`:
  ```javascript
  const BUDDY_CONFIG = {
    picovoiceAccessKey: 'your_key_here',
    serverUrl: window.location.origin,
    alwaysOnMode: true,
  };
  ```
  Add `config.js` to `.gitignore`.

---

## PHASE 2: Multi-Device Session Management

### 2.1 Create `server/device_manager.py`

```python
"""Multi-device session manager.

Ensures only one device is active at a time. When a new device connects,
the previous device is asked to yield. Tracks device metadata for
debugging and logging.

Rules:
1. If no device is active → new device connects immediately
2. If iPad is idle (wake word listening) and iPhone connects → iPad yields
3. If any device is mid-conversation → new device waits (or pre-empts based on priority)
4. Manual "Connect" button always takes priority
"""

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Awaitable

from loguru import logger


class DeviceType(str, Enum):
    IPAD = "ipad"       # Home station — lowest priority, resumes wake word on yield
    IPHONE = "iphone"   # Mobile — medium priority
    MAC = "mac"         # Desktop — medium priority
    UNKNOWN = "unknown"


@dataclass
class DeviceInfo:
    device_id: str
    device_type: DeviceType = DeviceType.UNKNOWN
    user_agent: str = ""
    connected_at: float = 0.0
    last_active: float = 0.0
    in_conversation: bool = False


class DeviceManager:
    def __init__(self):
        self._active: DeviceInfo | None = None
        self._devices: dict[str, DeviceInfo] = {}
        self._yield_callback: Callable[[str], Awaitable[None]] | None = None

    def set_yield_callback(self, callback: Callable[[str], Awaitable[None]]):
        """Set callback to send 'yield' signal to a device.
        
        callback(device_id) should send a WebRTC data channel message
        to the specified device telling it to disconnect.
        """
        self._yield_callback = callback

    @property
    def active_device_id(self) -> str | None:
        return self._active.device_id if self._active else None

    async def connect(self, device_id: str, device_type: str = "unknown", user_agent: str = "") -> bool:
        """Register a new device connection. Returns True if allowed.
        
        If another device is active, sends it a yield signal first.
        """
        dtype = DeviceType(device_type) if device_type in DeviceType.__members__.values() else DeviceType.UNKNOWN

        info = DeviceInfo(
            device_id=device_id,
            device_type=dtype,
            user_agent=user_agent,
            connected_at=time.time(),
            last_active=time.time(),
        )
        self._devices[device_id] = info

        # Yield previous device if needed
        if self._active and self._active.device_id != device_id:
            old_id = self._active.device_id
            logger.info(f"📱 Device {device_id} ({dtype}) taking over from {old_id}")
            if self._yield_callback:
                await self._yield_callback(old_id)
        else:
            logger.info(f"📱 Device {device_id} ({dtype}) connected")

        self._active = info
        return True

    def disconnect(self, device_id: str):
        """Remove a device connection."""
        if self._active and self._active.device_id == device_id:
            logger.info(f"📱 Device {device_id} disconnected")
            self._active = None
        self._devices.pop(device_id, None)

    def mark_active(self, device_id: str):
        """Mark a device as actively in conversation (prevents yield)."""
        if device_id in self._devices:
            self._devices[device_id].last_active = time.time()
            self._devices[device_id].in_conversation = True

    def mark_idle(self, device_id: str):
        """Mark a device as idle (can be yielded)."""
        if device_id in self._devices:
            self._devices[device_id].in_conversation = False
```

### 2.2 Update `server/bot.py` — Add Device Management + Data Channel

Add device manager integration and WebRTC data channel support to the existing bot.

Key changes:
1. Accept `device_id` from client during WebRTC signaling (pass as query param or in offer)
2. Create data channel for control messages (yield, state, tool_use, conversation_end)
3. On `on_client_connected`: register device with manager
4. On `on_client_disconnected`: unregister device
5. Conversation end detection: if 30s of silence → send `conversation_end` via data channel

```python
# Add to bot.py:

from device_manager import DeviceManager

device_mgr = DeviceManager()

# Conversation end detection — add as a custom frame processor
class ConversationEndDetector(FrameProcessor):
    """Detects prolonged silence and signals conversation end."""

    def __init__(self, timeout_seconds: float = 30.0):
        super().__init__()
        self.timeout = timeout_seconds
        self.last_activity = time.time()
        self._task = None

    async def process_frame(self, frame, direction):
        from pipecat.frames.frames import TranscriptionFrame, TextFrame

        # Any transcription or bot response resets the timer
        if isinstance(frame, (TranscriptionFrame, TextFrame)):
            self.last_activity = time.time()

        await self.push_frame(frame, direction)

    async def check_timeout(self) -> bool:
        """Returns True if conversation has timed out."""
        return (time.time() - self.last_activity) > self.timeout
```

### 2.3 Update `server/openclaw_llm.py` — Tool-Use Events

When OpenClaw uses a tool, forward the event to the client via data channel.

```python
# In _respond_via_openclaw, after getting the response:
# Check if the response mentions tool usage (OpenClaw may include tool metadata)
# This is a heuristic — exact detection depends on OpenClaw's response format.

# Simple approach: check for tool-related keywords in the response
TOOL_KEYWORDS = {
    'calendar': ['calendar', 'schedule', 'meeting', 'event', 'appointment'],
    'weather': ['weather', 'temperature', 'forecast', 'rain', 'sunny'],
    'email': ['email', 'inbox', 'message from', 'mail'],
    'lights': ['light', 'lamp', 'brightness', 'hue'],
    'search': ['search', 'found', 'according to', 'results'],
}

def _detect_tool(self, user_text: str) -> str | None:
    """Guess which tool might be used based on user's question."""
    lower = user_text.lower()
    for tool, keywords in TOOL_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            return tool
    return None
```

---

## PHASE 3: Server-Side Audio Hardening

### 3.1 Conversation End Detection

Add conversation end detection to `bot.py`. After 30 seconds of silence, send `conversation_end` to the client and close the connection.

The implementation depends on Pipecat's event system. Two approaches:

**Approach A: Background asyncio task**
```python
async def _monitor_silence(self, task, transport):
    """Background task that checks for prolonged silence."""
    while True:
        await asyncio.sleep(5)  # Check every 5 seconds
        if self._silence_detector.check_timeout():
            # Send goodbye via LLM
            # Then signal conversation end
            logger.info("⏰ Conversation timeout — ending session")
            # Queue a system message to make Buddy say goodbye
            await task.queue_frames([...])
            break
```

**Approach B: Pipecat event handler**
Check if Pipecat emits `UserStoppedSpeakingFrame` with timestamps, and track the gap.

**Recommended:** Approach A is more reliable. Start a background task when client connects, cancel it on disconnect or new speech.

### 3.2 Echo Cancellation Verification

The browser's `echoCancellation: true` should handle most cases. Add a server-side safety net:

```python
# In bot.py — mute STT processing while TTS is playing
# Pipecat's interruption handling already does this to some degree.
# But for extra safety, track TTS state:

class EchoGuard(FrameProcessor):
    """Suppresses STT results that arrive while TTS is playing.
    
    This prevents Buddy from hearing its own voice through the speaker
    and responding to itself.
    """

    def __init__(self):
        super().__init__()
        self._tts_playing = False
        self._tts_ended_at = 0.0
        self._guard_duration = 0.5  # Ignore STT for 500ms after TTS ends

    async def process_frame(self, frame, direction):
        from pipecat.frames.frames import (
            TTSStartedFrame, TTSStoppedFrame, TranscriptionFrame
        )

        if isinstance(frame, TTSStartedFrame):
            self._tts_playing = True
        elif isinstance(frame, TTSStoppedFrame):
            self._tts_playing = False
            self._tts_ended_at = time.time()
        elif isinstance(frame, TranscriptionFrame):
            # Suppress transcriptions during TTS or shortly after
            if self._tts_playing:
                return  # Drop frame
            if (time.time() - self._tts_ended_at) < self._guard_duration:
                return  # Drop frame

        await self.push_frame(frame, direction)
```

**NOTE:** Check Pipecat's actual frame types for TTS start/stop. They may be named differently (`TTSAudioRawFrame`, `TTSStartFrame`, etc.). Search the installed package.

### 3.3 VAD Profile Switching

If Pipecat supports runtime VAD parameter changes, switch between relaxed (conversation) and strict (always-on) profiles:

```python
# In config.py:
VAD_CONVERSATION = {
    'threshold': 0.4,
    'min_speech_duration_ms': 200,
    'min_silence_duration_ms': 600,
    'padding_duration_ms': 300,
}

VAD_STRICT = {
    'threshold': 0.7,
    'min_speech_duration_ms': 500,
    'min_silence_duration_ms': 1000,
    'padding_duration_ms': 300,
}
```

Apply `VAD_CONVERSATION` when a client is connected, `VAD_STRICT` during wake-word-only mode. If runtime switching isn't supported, use the conversation profile always (it's fine for v1).

---

## PHASE 4: iPad Kiosk Setup Guide

### 4.1 Create `docs/ipad-setup.md`

```markdown
# iPad Home Station Setup Guide

## What You Need

- Any iPad running iPadOS 15+ (even an old one is fine)
- A charging stand (portrait orientation recommended)
- Same WiFi network as the Mac Mini running Buddy

## Step 1: iPad Settings

### Display & Brightness
- **Auto-Lock:** Never
- **Brightness:** 30-40% (or Auto)

### Accessibility → Guided Access
- **Enable** Guided Access
- Set a passcode you'll remember
- This locks the iPad to Buddy so no one accidentally exits

### Notifications
- Go to **Focus** → create a "Buddy" focus mode
- Block all notifications (or allow only critical ones)
- This prevents notification sounds from triggering the mic

### General → AirPlay & Handoff
- Disable Handoff

### Battery
- Enable "Optimized Battery Charging"
- Keep iPad plugged in permanently — the optimization protects the battery

## Step 2: Open Buddy

1. Open Safari
2. Go to `http://<mac-mini-ip>:7860/client` (or :8080 if using separate client server)
3. Tap **Share** → **Add to Home Screen**
4. Name it "Buddy"
5. Open Buddy from the home screen (full-screen mode)

## Step 3: Enable Kiosk Mode

1. Open Buddy PWA from home screen
2. Triple-click the **Side Button** (or Home Button) → starts Guided Access
3. Tap **Start**
4. iPad is now locked to Buddy

To exit: Triple-click Side Button → enter passcode → End

## Step 4: Test

- Say "Hey Buddy" — should hear a chime and get a response
- Wait 2 minutes — screen should dim (ambient mode with clock)
- Say "Hey Buddy" again — screen wakes up
- Pull out your iPhone, open Buddy PWA there → iPad should yield

## Troubleshooting

- **Mic not working:** Check Settings → Safari → Microphone → Allow
- **"Hey Buddy" not triggering:** Speak clearly from within 3 meters. Check browser console for Porcupine errors.
- **Screen going black:** Verify Auto-Lock is set to Never
- **Can't exit Guided Access:** Triple-click Side Button, enter passcode, tap End
- **No sound from Buddy:** Check iPad isn't in silent mode (check Settings → Sounds)
```

---

## PHASE 5: Validation Checklist

Run through each item. All must pass before considering Week 3 complete.

### Wake Word
- [ ] "Hey Buddy" triggers from 1 meter
- [ ] "Hey Buddy" triggers from 3+ meters
- [ ] Wake word chime plays on detection
- [ ] After detection, WebRTC connection starts automatically
- [ ] During conversation, wake word is paused (no double triggers)
- [ ] After conversation ends, wake word resumes
- [ ] Without `.ppn` model file: falls back to push-to-talk gracefully

### Always-On / iPad
- [ ] Screen stays on (doesn't sleep) for 30+ minutes
- [ ] Ambient mode activates after 2 minutes idle
- [ ] Ambient mode shows clock
- [ ] Voice activity wakes from ambient mode
- [ ] Touch wakes from ambient mode
- [ ] Auto-reconnect after server restart (within 30s)
- [ ] Auto-reconnect after WiFi dropout
- [ ] Guided Access locks iPad to Buddy PWA

### Multi-Device
- [ ] iPad active → iPhone connects → iPad yields
- [ ] iPhone disconnects → iPad resumes wake word
- [ ] Two browsers can't both be active simultaneously
- [ ] Data channel `yield` message received and processed

### Audio
- [ ] No echo: Buddy speaking doesn't trigger self-response
- [ ] Background TV/music doesn't trigger false wake words (< 1 per hour)
- [ ] Background noise doesn't trigger false STT during conversation
- [ ] Conversation timeout: 30s silence → Buddy says goodbye → disconnects

### UX
- [ ] Wake word chime plays (rising two-tone)
- [ ] Connect sound plays
- [ ] Disconnect sound plays
- [ ] Tool indicator shows during tool use
- [ ] Error sound on connection failure

### Security
- [ ] No API keys in client-side source (except Picovoice in gitignored config.js)
- [ ] Server only accessible on LAN
- [ ] No audio recorded/stored by default

---

## PHASE 6: Git

```bash
cd /Users/elie/.openclaw/workspace/projects/buddy
git add -A
git commit -m "Week 3: Wake word, iPad home station, multi-device, audio hardening

- 'Hey Buddy' wake word via Picovoice Porcupine (on-device WASM)
- Always-on mode: auto-reconnect, wake lock, ambient display
- Multi-device management: one active device, clean handoff
- Echo cancellation guard, conversation timeout
- Audio feedback sounds (Web Audio API procedural)
- Tool-use indicators on client
- iPad kiosk setup guide"
git push origin main
```

---

## KNOWN ISSUES & EDGE CASES

1. **Porcupine SDK version:** The CDN URL `@picovoice/porcupine-web@3` may need updating. Check https://www.npmjs.com/package/@picovoice/porcupine-web for the latest version. The API surface (constructor, `process()` method, frame handling) may differ between v2 and v3.

2. **ScriptProcessorNode deprecation:** `ScriptProcessorNode` is deprecated in favor of `AudioWorkletNode`. It still works in all browsers as of 2026. For production, migrate to AudioWorklet. For v1, it's fine.

3. **iOS Safari Wake Lock:** Safari on iOS supports the Screen Wake Lock API as of iOS 16.4. On older iPads, the only way to prevent sleep is the Settings → Auto-Lock → Never approach.

4. **Mic permissions on PWA:** When opened from the home screen, the PWA may not have microphone permission cached. The user needs to grant it on first launch. Safari will remember it for subsequent launches from the same origin.

5. **Multiple conversation sessions:** The device manager prevents multiple active connections, but if two devices connect within milliseconds of each other, there could be a race condition. The simple "last writer wins" approach handles this acceptably for a single-user system.

6. **Porcupine access key exposure:** The Picovoice Access Key is in client-side JavaScript. This is acceptable for LAN-only use. If exposed to the internet, someone could use your key to run Porcupine (but not much else). Picovoice's free tier has generous limits.

7. **iPad thermal throttling:** Running WebRTC + Porcupine WASM continuously may cause thermal throttling on older iPads. Monitor battery health. If too hot, the wake word processing can be throttled (check every 64ms instead of 32ms).

---

## WHAT COMES AFTER WEEK 3

These are NOT in scope for this buildspec:

- **Week 4:** Local fallback chain (Whisper.cpp STT + Piper TTS + Qwen LLM), latency optimization, voice cloning, production polish
