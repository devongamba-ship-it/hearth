# Voice check-in — Raspberry Pi setup (BAT-30)

The resident check-in is the step that kills false positives: when the fall
machine reaches `PROBABLE` it plays *"Are you OK? Say 'I'm fine', or help is
coming,"* listens for 45 s, and routes the answer:

| What the resident does | Outcome |
| --- | --- |
| says "I'm fine" / "I'm OK" / "yes" | recovered → back to `NORMAL` (logged `checkin_answered`) |
| shouts "help" | **immediate** `CONFIRMED_FALL` (logged `help_keyword`) — no waiting out the window |
| says nothing / only TV noise | window times out → `CONFIRMED_FALL` (logged `checkin_timeout`) |

Two implementations share one seam (`CheckInProvider.poll → PENDING | OK | HELP`):

- **`stub`** — the v1 default. No microphone; every check-in times out. This is
  what runs in CI and on any node without audio hardware.
- **`local_audio`** — this guide. Mic capture during the check-in window only,
  offline keyword spotting, nothing recorded.

Switching between them is **config-only** (`detection.yaml`); no code changes.

## Privacy guarantees (enforced in code, not just policy)

- The microphone is opened **only** for an active check-in window and is
  hard-released the instant an answer is recognised, the 45 s window elapses, or
  the check-in is cancelled — whichever comes first. Each open/close is written
  to the audit log with a timestamp.
- Recognition is **yes/no-class only** (a short keyword grammar). No audio is
  written to disk, none leaves the device — only the three-way outcome does.
- Prompts are **pre-rendered audio files**; there is no runtime text-to-speech.

## Pilot hardware

- **Raspberry Pi 4** (or Pi Zero 2 W) on the same wall-wart install as a sensing
  node.
- **USB conference speakerphone puck** with a far-field mic array and a loud
  speaker — e.g. an **Anker PowerConf**-class device. The mic array is what lets
  an injured resident be heard from the floor across the room.

Smart speakers (Alexa/Nest) are deliberately **not** used for the core loop:
third-party skills cannot proactively open the mic (the resident would have to
remember a wake word while injured), always-on cloud mics contradict the privacy
brand, and the platform risk is proven (Alexa Together was sunset).

## One-time setup on the Pi

1. **System audio libs** (PortAudio backs `sounddevice`):

   ```bash
   sudo apt-get update && sudo apt-get install -y libportaudio2
   ```

2. **Python deps** (kept out of the default install / CI on purpose):

   ```bash
   pip install sounddevice vosk
   ```

3. **Offline speech model** — a small English Vosk model is enough for the
   keyword grammar:

   ```bash
   mkdir -p ~/hearth/models && cd ~/hearth/models
   curl -LO https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
   unzip vosk-model-small-en-us-0.15.zip
   # -> ~/hearth/models/vosk-model-small-en-us-0.15
   ```

## Finding and picking the speakerphone

List the audio devices the Pi can see and note the **input** device whose name
matches your puck (e.g. contains `PowerConf`):

```bash
python3 -c "import sounddevice as sd; print(sd.query_devices())"
```

```
> 1 Anker PowerConf S330, ALSA (1 in, 0 out)
< 2 Anker PowerConf S330, ALSA (0 in, 2 out)
  ...
```

`device` in the config matches by name **substring** (or you can give the
numeric index), so `PowerConf` is usually enough. Confirm a quick capture works:

```bash
# 3-second test recording to verify the device + sample rate; delete it after.
python3 -c "import sounddevice as sd, sys; sd.default.device='PowerConf'; \
sd.rec(int(3*16000), samplerate=16000, channels=1, dtype='int16'); sd.wait(); \
print('capture OK')"
```

## Enable it

Point the ingest service at a `detection.yaml` (`HEARTH_DETECTION_FILE=...`) with:

```yaml
checkin:
  provider: local_audio
  device: PowerConf          # name substring or numeric index from query_devices()
  listen_window_secs: 45
  confidence_threshold: 0.6
  vad_energy_threshold: 350.0 # raise if a noisy room trips the VAD
  model_path: /home/pi/hearth/models/vosk-model-small-en-us-0.15
  samplerate: 16000
```

Restart the fall-machine poller (`python -m ingest.fall_machine`). On the next
check-in you'll see `checkin mic_open` / `checkin mic_close` audit lines bracket
the window. Nothing else about the install changes — the machine, escalation,
and caregiver app are identical to the stub path.

## Tuning notes

- **`vad_energy_threshold`** gates "is this speech?" by frame RMS. Too low and a
  loud TV trips it (harmless — it still won't *recognise* a keyword, but it keeps
  the mic processing); too high and a faint, distant voice is missed. Start at
  the default and adjust against the room.
- **`confidence_threshold`** is the floor below which a recognised word is
  ignored. Raise it if you see spurious recoveries; lower it if real answers are
  being missed.
- **`listen_window_secs`** should stay ≤ the machine's `checkin_timeout_secs` so
  the mic is always released by the time the window confirms.

## v2 cost-down (not this issue)

An **ESP32-S3 embedded voice node** (I²S MEMS mic + 3 W amp, ESP-SR offline
command recognition, ~$15 BOM, same wall-wart install as the sensing nodes) can
replace the Pi + puck later. It implements the same `poll → PENDING | OK | HELP`
seam, so it too is a config/provider swap with no machine changes.
