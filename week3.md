# Week 3: Home Device + Wake Word Detection

**Goal:** Set up an iPad as a dedicated Buddy home station and add "Hey Buddy" wake word so it's always listening. Walk into a room and start talking — no button tap needed.

**Success criteria:** iPad on a stand in the living room, always on, says "Hey Buddy" and it responds within 2 seconds. Also works from iPhone and Mac without wake word (push-to-talk).

---

## Day 1: Wake Word Detection — Porcupine Integration

### Targets
- [ ] Integrate Picovoice Porcupine for "Hey Buddy" wake word
- [ ] Wake word runs on-device (client-side), zero server cost
- [ ] After wake word triggers, mic streams to Buddy server until conversation ends

### Steps

**1. Porcupine overview**
- Porcupine by Picovoice — lightweight on-device wake word engine
- Runs in browser via WebAssembly (no server needed)
- Free tier: 3 custom wake words, unlimited use on 3 devices
- Custom keyword trained via Picovoice Console: https://console.picovoice.ai
- ~5ms processing per audio frame, <1MB model size

**2. Create custom "Hey Buddy" wake word**
- Sign up at https://console.picovoice.ai
- Go to Porcupine → Train keyword
- Type "Hey Buddy", select platforms: Web (WASM), iOS, macOS
- Download the `.ppn` model file(s)
- Store in `client/web/models/hey-buddy_en_wasm.ppn`

**3. Client-side integration**

```javascript
// wakeword.js — Porcupine wake word detection

import { PorcupineWorker } from '@picovoice/porcupine-web';

class WakeWordDetector {
    constructor(onWake) {
        this.onWake = onWake;
        this.porcupine = null;
        this.isListening = false;
    }

    async start(accessKey) {
        this.porcupine = await PorcupineWorker.create(
            accessKey,
            [{ 
                publicPath: 'models/hey-buddy_en_wasm.ppn',
                label: 'hey_buddy'
            }],
            (detection) => {
                if (detection.label === 'hey_buddy') {
                    console.log('🎤 Wake word detected!');
                    this.onWake();
                }
            }
        );
        this.isListening = true;
    }

    async stop() {
        if (this.porcupine) {
            await this.porcupine.terminate();
            this.porcupine = null;
        }
        this.isListening = false;
    }
}
```

**4. Client state machine update**

```
[Always On — Wake Word Listening]
        |
  "Hey Buddy" detected
        |
        v
[Connected — Streaming to Server]
        |
  Conversation ends (silence timeout or user says "bye")
        |
        v
[Always On — Wake Word Listening]
```

- Wake word runs continuously on-device (uses ~2% CPU)
- After detection: open WebRTC connection, stream audio to server
- After conversation ends: close WebRTC, return to wake word listening
- Conversation timeout: 30s of silence → auto-disconnect

**5. Conversation end detection**
The server should signal when to close the connection:
- User says "bye", "thanks", "that's all" → server sends goodbye, then closes
- 30s of silence → server sends "I'll be here if you need me" and closes
- User can also manually tap disconnect

### Deliverable
Say "Hey Buddy" from 3+ meters away and it activates. No button needed.

---

## Day 2: iPad Home Station Setup

### Targets
- [ ] iPad configured as always-on Buddy station
- [ ] Guided Access locks iPad to Buddy PWA
- [ ] Screen dims but stays on (never sleeps)
- [ ] Auto-reconnects after power loss or Wi-Fi dropout

### Steps

**1. Physical setup**
- iPad on a charging stand (portrait orientation)
- Positioned in living room / kitchen / wherever Elie spends time
- Connected to power permanently
- Good microphone line of sight (not blocked by furniture)

**2. iPad settings for kiosk mode**

```
Settings → Display & Brightness:
  - Auto-Lock: Never
  - Brightness: Auto (or ~30% to prevent burn-in)

Settings → Accessibility → Guided Access:
  - Enable Guided Access
  - Set a passcode
  - This locks the iPad to the Buddy PWA — prevents accidental exits

Settings → General → AirPlay & Handoff:
  - Disable Handoff (prevents UI interruptions)

Settings → Notifications:
  - Disable notifications for all apps (or at least silence them)
  - This prevents notification sounds from triggering the mic

Settings → Battery:
  - "Optimized Battery Charging" ON (protects battery at 100% plug-in)
```

**3. Always-on client behavior**
Update the PWA client for always-on mode:

```javascript
// always-on.js — iPad kiosk mode features

class AlwaysOnManager {
    constructor(client) {
        this.client = client;
        this.reconnectDelay = 1000; // Start at 1s
        this.maxReconnectDelay = 30000; // Cap at 30s
    }

    // Auto-reconnect on connection loss
    enableAutoReconnect() {
        this.client.onDisconnect = async () => {
            console.log(`Reconnecting in ${this.reconnectDelay}ms...`);
            await this._sleep(this.reconnectDelay);
            this.reconnectDelay = Math.min(
                this.reconnectDelay * 2, 
                this.maxReconnectDelay
            );
            try {
                await this.client.connect();
                this.reconnectDelay = 1000; // Reset on success
            } catch (e) {
                this.enableAutoReconnect(); // Retry
            }
        };
    }

    // Keep screen alive (prevent dimming)
    async enableWakeLock() {
        if ('wakeLock' in navigator) {
            try {
                this.wakeLock = await navigator.wakeLock.request('screen');
                // Re-acquire on visibility change
                document.addEventListener('visibilitychange', async () => {
                    if (document.visibilityState === 'visible') {
                        this.wakeLock = await navigator.wakeLock.request('screen');
                    }
                });
            } catch (e) {
                console.warn('Wake Lock not available:', e);
            }
        }
    }

    // Ambient display mode (dim screen, show clock)
    enableAmbientMode() {
        let idleTimer;
        const dimScreen = () => {
            document.body.classList.add('ambient');
            // Show minimal clock + orb animation
        };
        const wakeScreen = () => {
            document.body.classList.remove('ambient');
        };

        // Dim after 2 minutes of inactivity
        const resetTimer = () => {
            clearTimeout(idleTimer);
            wakeScreen();
            idleTimer = setTimeout(dimScreen, 120_000);
        };

        // Any interaction or voice activity wakes screen
        ['touchstart', 'mousemove', 'keydown'].forEach(e =>
            document.addEventListener(e, resetTimer)
        );
        // Also wake on voice detection
        this.client.onVoiceActivity = resetTimer;
        resetTimer();
    }

    _sleep(ms) {
        return new Promise(r => setTimeout(r, ms));
    }
}
```

**4. Ambient display CSS**

```css
/* Ambient mode — dimmed, shows clock */
.app.ambient {
    opacity: 0.3;
    transition: opacity 1s ease;
}

.app.ambient .orb {
    animation: pulse 6s ease-in-out infinite; /* Slower pulse */
}

.ambient-clock {
    display: none;
    font-size: 4rem;
    font-weight: 200;
    color: var(--text-dim);
    position: absolute;
    bottom: 20%;
}

.app.ambient .ambient-clock {
    display: block;
}
```

**5. Audio optimization for iPad**
- Use `echoCancellation: true` in getUserMedia (critical for speaker → mic feedback)
- Set audio output to iPad speaker (not Bluetooth)
- Test microphone pickup range — should work at conversational distance (2-3m)

### Deliverable
iPad on a stand, always on, wake-word ready. Dim ambient display when idle, bright when conversing.

---

## Day 3: Multi-Device Session Management

### Targets
- [ ] Only one device active at a time (no echo/conflicts)
- [ ] If iPhone activates while iPad is listening, iPad yields
- [ ] Server tracks which device is currently connected
- [ ] Graceful handoff between devices

### Steps

**1. Device identification**
Each client registers with a device ID:

```javascript
// Generate or load persistent device ID
function getDeviceId() {
    let id = localStorage.getItem('buddy_device_id');
    if (!id) {
        id = `buddy-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
        localStorage.setItem('buddy_device_id', id);
    }
    return id;
}
```

**2. Server-side device management**

```python
# device_manager.py

class DeviceManager:
    def __init__(self):
        self.active_device = None  # device_id of currently connected client
        self.devices = {}  # device_id → {name, type, last_seen}

    def can_connect(self, device_id: str) -> bool:
        """Check if this device can take the active slot."""
        if self.active_device is None:
            return True
        if self.active_device == device_id:
            return True
        # New device pre-empts (most recent wins)
        return True

    def connect(self, device_id: str):
        if self.active_device and self.active_device != device_id:
            self._disconnect_device(self.active_device)
        self.active_device = device_id

    def disconnect(self, device_id: str):
        if self.active_device == device_id:
            self.active_device = None

    def _disconnect_device(self, device_id: str):
        # Send disconnect signal to the old device
        pass
```

**3. Priority rules**
- Active conversation always wins (if iPad is mid-conversation, iPhone can't steal)
- Wake word activation from any device starts a connection
- Manual "Connect" button always takes priority
- iPad goes back to wake word listening when another device connects

**4. Data channel for control messages**
Use WebRTC data channel for device coordination:

```javascript
// In client — add data channel
const dc = pc.createDataChannel('control');
dc.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    if (msg.type === 'yield') {
        // Another device took over — disconnect gracefully
        client.disconnect();
        status.textContent = 'Another device is active';
    }
};
```

### Deliverable
Talk to Buddy from iPhone, iPad automatically goes quiet. Finish on iPhone, iPad resumes listening.

---

## Day 4: Always-Listening Mode + Audio Pipeline Optimization

### Targets
- [ ] Server-side VAD tuning for always-on use
- [ ] Noise floor calibration for iPad's environment
- [ ] Echo cancellation verification (Buddy speaks → mic doesn't re-trigger)
- [ ] Battery/thermal optimization for continuous operation

### Steps

**1. VAD tuning for always-on**
Always-listening is more demanding than push-to-talk:
- Higher threshold needed (reject TV, music, distant conversations)
- Longer minimum speech duration (reject one-word false triggers)
- Post-wake-word mode should be more sensitive than pre-wake-word

```python
# Two VAD profiles
VAD_WAKE_WORD = {
    'threshold': 0.7,          # High — only confident speech
    'min_speech_duration': 0.5, # At least 500ms
    'min_silence_duration': 1.0,
    'padding_duration': 0.3,
}

VAD_CONVERSATION = {
    'threshold': 0.4,          # Normal — in active conversation
    'min_speech_duration': 0.2,
    'min_silence_duration': 0.6,
    'padding_duration': 0.3,
}
```

**2. Echo cancellation testing**
Critical test: Buddy is speaking through iPad speaker → user's voice should not trigger a re-response.

Test procedure:
1. Ask Buddy a question that gets a long answer
2. While Buddy speaks, stay silent
3. Verify: Buddy finishes speaking, doesn't trigger itself
4. If self-triggering: increase VAD threshold during TTS playback, or use hardware echo cancellation

Mitigation strategies:
- Browser's built-in `echoCancellation: true` (usually sufficient)
- Server-side: mute STT input while TTS is playing
- Client-side: mute mic during audio playback (aggressive but reliable)

**3. Noise floor calibration**
On first setup, calibrate for the room's ambient noise:

```javascript
// Run 5 seconds of silence to measure room noise
async function calibrateNoiseFloor() {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const ctx = new AudioContext();
    const src = ctx.createMediaStreamSource(stream);
    const analyzer = ctx.createAnalyser();
    src.connect(analyzer);
    
    const data = new Uint8Array(analyzer.frequencyBinCount);
    let sum = 0, count = 0;
    
    return new Promise(resolve => {
        const interval = setInterval(() => {
            analyzer.getByteFrequencyData(data);
            sum += data.reduce((a, b) => a + b, 0) / data.length;
            count++;
            if (count >= 50) { // ~5 seconds
                clearInterval(interval);
                stream.getTracks().forEach(t => t.stop());
                resolve(sum / count);
            }
        }, 100);
    });
}
```

**4. Thermal management**
iPad running WebRTC continuously will get warm:
- Reduce video processing (audio-only WebRTC uses minimal CPU)
- Screen brightness at 30% or lower in ambient mode
- Monitor iPad temperature via battery health
- If too hot: reduce wake word processing frequency from every 32ms to every 64ms

### Deliverable
iPad runs cool and quiet in always-listening mode. No false triggers from TV, music, or Buddy's own voice.

---

## Day 5: Conversation UX Polish

### Targets
- [ ] Buddy greeting when someone enters the room (optional, via motion detection or schedule)
- [ ] Conversation context indicator ("Buddy is using your calendar...")
- [ ] Audio feedback sounds (connect chime, wake word ack, error boop)
- [ ] Smooth transitions between states

### Steps

**1. Audio feedback sounds**
Short, subtle sounds for state transitions:

| Event | Sound | Duration |
|-------|-------|----------|
| Wake word detected | Rising chime | 200ms |
| Connected | Soft ding | 150ms |
| Disconnected | Descending tone | 200ms |
| Error | Low buzz | 300ms |
| Tool executing | Subtle tick | 100ms |

Generate procedurally with Web Audio API or use short audio files:

```javascript
// Procedural sounds via Web Audio API
function playChime(ctx, frequency = 880, duration = 0.15) {
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.frequency.value = frequency;
    gain.gain.value = 0.1;
    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + duration);
    osc.start();
    osc.stop(ctx.currentTime + duration);
}
```

**2. Visual tool indicator**
When Buddy is using a tool (calendar, weather, etc.), show a subtle indicator:

```
 🗓 Checking your calendar...
```

This requires the server to send tool-use events via data channel:

```python
# In openclaw_llm.py, when tool use is detected
await data_channel.send(json.dumps({
    'type': 'tool_use',
    'tool': 'calendar',
    'status': 'running'
}))
```

**3. Conversation flow improvements**
- After a long silence: "Still there?" (only once per session)
- Natural goodbye: "See you later" → Buddy says bye → disconnects
- Resume greeting: "Hey again" when reconnecting within 30 min
- Error recovery: "Sorry, let me try that again" instead of silence

### Deliverable
Buddy feels polished. Audio cues make state transitions clear. Tool usage is transparent.

---

## Day 6: Security + Network Hardening

### Targets
- [ ] Server only accessible from LAN (or Tailscale)
- [ ] API keys cannot be extracted from client
- [ ] Audio is not stored by default
- [ ] Optional: Tailscale setup for remote access

### Steps

**1. Network security**
- Buddy server binds to `0.0.0.0` but should only be accessible from LAN
- macOS firewall: allow connections only from LAN subnet
- No public internet exposure

```bash
# Verify no external exposure
# From outside your network, this should fail:
curl http://<public-ip>:7860/client
```

**2. Tailscale for remote access (optional)**
If Elie wants to use Buddy from outside home:
- Install Tailscale on Mac Mini (if not already)
- Buddy becomes accessible at `http://100.x.x.x:7860/client`
- Tailscale provides automatic HTTPS via MagicDNS
- This also solves the iPhone mic-over-HTTP issue

**3. Client-side security**
- No API keys in client code (all API calls happen server-side)
- No audio recording or logging by default
- PWA service worker doesn't cache sensitive data

**4. Optional: audio logging for debugging**
Add a debug mode that logs transcriptions (not raw audio) for debugging:

```python
# In config.py
DEBUG_LOG_TRANSCRIPTS = os.getenv("BUDDY_DEBUG_TRANSCRIPTS", "false").lower() == "true"

# In openclaw_llm.py
if config.DEBUG_LOG_TRANSCRIPTS:
    with open("transcripts.log", "a") as f:
        f.write(f"{timestamp} USER: {text}\n")
        f.write(f"{timestamp} BUDDY: {response}\n")
```

### Deliverable
Secure by default. No data leaks. Optional remote access via Tailscale.

---

## Day 7: Full Integration Test + Documentation

### Targets
- [ ] All three devices working: Mac browser, iPhone PWA, iPad station
- [ ] Wake word works on iPad
- [ ] Device handoff works cleanly
- [ ] Update README with Week 3 features
- [ ] Update plan.md with progress

### Steps

**1. Full system test**

| Test | Expected |
|------|----------|
| iPad: "Hey Buddy, what time is it?" | Wake word triggers, responds with time |
| iPad: idle for 5 min | Screen dims to ambient mode |
| iPad idle → "Hey Buddy" | Screen wakes, responds |
| iPhone: open PWA, tap Connect, talk | Works, iPad yields |
| iPhone: disconnect | iPad resumes wake word listening |
| Mac: open browser, talk | Works alongside or steals from iPad |
| Buddy speaking → stay silent | No self-triggering (echo cancellation) |
| Play music on TV near iPad | No false wake word triggers |
| Power cycle iPad | Auto-reconnects to Buddy |
| Kill Buddy server, restart | Clients auto-reconnect |

**2. Performance benchmarks**

| Metric | Target | Measured |
|--------|--------|----------|
| Wake word → first audio | <2.5s | ___ |
| Push-to-talk → first audio | <1.5s | ___ |
| iPad idle CPU usage | <5% | ___ |
| iPad active CPU usage | <15% | ___ |
| Server memory usage (idle) | <200MB | ___ |
| Server memory usage (active) | <500MB | ___ |

**3. Update documentation**
- README.md: add iPad setup instructions, wake word setup, device management
- plan.md: mark Week 3 complete, update status

### Deliverable
Three-device system working reliably. iPad is a true always-on home companion.

---

## Week 3 Exit Criteria

| # | Criteria | Target |
|---|----------|--------|
| 1 | Wake word detection | "Hey Buddy" triggers from 3m+ |
| 2 | iPad always-on station | Runs 24h without intervention |
| 3 | Ambient display | Dims when idle, wakes on voice |
| 4 | Multi-device handoff | Clean yield when new device connects |
| 5 | Echo cancellation | No self-triggering |
| 6 | False trigger rate | <1 per hour in typical home environment |
| 7 | Auto-reconnect | Recovers from network/server drops |
| 8 | Security | LAN-only, no key exposure, no audio storage |

## What's NOT in Week 3
- ❌ Local fallback chain (Week 4)
- ❌ Voice cloning (nice-to-have)
- ❌ Raspberry Pi hardware build (future)
- ❌ Multi-room audio (future)
- ❌ Proactive alerts ("You have a meeting in 10 minutes") — future
- ❌ Music playback / Spotify integration (future)

---

## Quick Reference
- **Picovoice Console:** https://console.picovoice.ai
- **Porcupine Web SDK:** https://github.com/Picovoice/porcupine/tree/master/binding/web
- **iPad Guided Access:** Settings → Accessibility → Guided Access
- **Screen Wake Lock API:** https://developer.mozilla.org/en-US/docs/Web/API/Screen_Wake_Lock_API
- **Tailscale:** https://tailscale.com/download
