# Jarvis

A local voice assistant for macOS. Say "Jarvis", ask for something, and it answers in a dry British voice and does the work on your Mac. Through Apple Shortcuts it can also reach your iPhone and iPad.

```
 "Jarvis"            speech              text                tool calls            reply
 ─────────▶ Porcupine ─────▶ recorder ─────▶ faster-whisper ─────▶ Claude ◀──────▶ tools.py ─────▶ say -v Daniel
            (local)          (silence         (local)              (API)      (AppleScript,       (local)
                              detection)                                       shortcuts, etc.)
```

Only the Claude API call leaves your machine. The wake word, recording, transcription, tools and speech all run locally.

## Project layout

| File | What it does |
|---|---|
| `jarvis.py` | Main loop: wake word → record → transcribe → Claude tool loop → speak. Also holds the system prompt and the Claude client. |
| `tools.py` | Every tool (the "hands") and the Anthropic tool schemas. Run it directly to test one tool. |
| `audio.py` | Microphone, Porcupine/openWakeWord wake word, silence-detecting recorder, faster-whisper. |
| `tts.py` | Speech output. `say` with Daniel by default, plus an ElevenLabs engine you can switch on. |
| `.env.example` | Template for your keys and settings. Copy it to `.env`. |
| `RECOMMENDED_SHORTCUTS.md` | The Apple Shortcuts to create so Jarvis can reach your iPhone and iPad. |

## Setup

Requires an Apple Silicon Mac and Python 3.11 or newer.

**1. Get Python 3.11+** (skip this if `python3 --version` already shows 3.11 or newer):

```bash
brew install python@3.12
```

**2. Create the virtualenv and install dependencies:**

```bash
cd jarvis
```

```bash
python3.12 -m venv .venv
```

```bash
source .venv/bin/activate
```

```bash
pip install -r requirements.txt
```

`sounddevice` ships its own PortAudio on macOS, so you don't need to install it separately.

**3. Add your keys:**

```bash
cp .env.example .env
```

Then open `.env` in an editor and fill in:

- `ANTHROPIC_API_KEY`: required. Create one at <https://console.anthropic.com/settings/keys>.
- `PICOVOICE_ACCESS_KEY`: optional but recommended. Free at <https://console.picovoice.ai/>. It enables Porcupine's built-in "Jarvis" wake word. If you leave it empty, Jarvis uses the free openWakeWord model, and the phrase becomes **"Hey Jarvis"**.

`.env` is in `.gitignore`, so your keys never get committed.

**4. Check the Daniel voice is installed:**

```bash
say -v Daniel "Good evening, sir."
```

If you get an error, go to System Settings → Accessibility → Spoken Content → System Voice → Manage Voices… → English (UK) → Daniel. Jarvis falls back to the default voice if Daniel is missing.

## Run

```bash
python jarvis.py
```

Jarvis prints `🎙 Listening…` and says "Jarvis online." Then:

- "Jarvis… what's my battery?"
- "Jarvis, open Safari."
- "Jarvis, set the volume to thirty."
- "Jarvis, remind me to call Pepper tomorrow at nine."
- "Jarvis, text Happy that I'm running late." (Jarvis reads the message back and waits for your "yes".)
- "That will be all." (quits)

You can talk straight through ("Jarvis what's the time") or pause after "Jarvis". You'll hear a *tink* when it starts recording, and recording stops after about a second of silence.

**Other modes:**

```bash
python jarvis.py --text          # type instead of talk (good for testing tools and the API key)
```

```bash
python jarvis.py --ptt           # push-to-talk: press Enter instead of using the wake word
```

```bash
python jarvis.py --list-devices  # then: python jarvis.py --device 2
```

**Test one tool without Claude:**

```bash
python tools.py get_battery_status
```

```bash
python tools.py open_app '{"name": "Safari"}'
```

The first run downloads the Whisper model (about 150 MB for `base.en`) and, if you're using it, the openWakeWord model. After that, both work offline.

## macOS permissions

macOS grants these to the **app that runs Python**: Terminal, iTerm, VS Code, and so on. Grant them to that app, and fully quit and reopen it afterwards.

Open them in **System Settings → Privacy & Security**:

| Permission | Why | What happens |
|---|---|---|
| **Microphone** | Wake word and speech. | macOS prompts on first launch. If you missed the prompt, switch your terminal on under Microphone. |
| **Accessibility** | AppleScript that sends keystrokes through System Events, including the brightness fallback. | Add your terminal app under Accessibility and switch it on. |
| **Automation** | Control Music, Messages, Reminders, Calendar, Contacts, Finder and System Events. | macOS asks the first time Jarvis touches each app; click OK. Review them under Automation → your terminal. |
| Contacts, Reminders, Calendars | Look up who "Pepper" is, and create reminders and events. | Prompted on first use. |

If a tool fails because a permission is missing, Jarvis says so out loud (for example, "Automation permission missing") instead of claiming success.

## How it behaves

- **Model.** `claude-sonnet-5-5` at `effort: low`, so voice replies come back fast. Change it with `JARVIS_MODEL` and `JARVIS_EFFORT` in `.env`.
- **Memory.** Jarvis keeps a rolling history of the last 10 exchanges, so "turn it up a bit more" works. It resets each time you restart.
- **Honesty.** Every tool returns `{"ok": true|false, ...}`. The system prompt tells Claude to claim success only when `ok` is true. Errors are passed back to Claude as tool errors, and Claude explains them.
- **Confirmations.** These are enforced in code, not left to Claude. Jarvis asks out loud and waits for a spoken "yes" (or "go ahead", "send it", and so on) before:
  - sending any iMessage
  - running a shortcut whose name sounds like it sends, calls, deletes or pays (Text, Call, Mail…)
  - running AppleScript that deletes, sends, quits, shuts down, runs shell commands or types keystrokes (set `JARVIS_CONFIRM_APPLESCRIPT=always` to confirm every script)

  Anything other than a clear yes counts as no.
- **Failure handling.**
  - A missing API key, a rejected key, rate limits, no internet and API errors each get a spoken explanation, and the loop keeps running.
  - A tool that raises an exception or times out returns an error to Claude rather than crashing Jarvis.
- **Safety fallback.** Requests go out with the API's server-side `fallbacks: "default"` (beta `server-side-fallback-2026-07-01`). If a safety classifier declines a request, the API retries it on a fallback model. If your account doesn't support it, Jarvis turns it off automatically.

## Tools

| Tool | Implementation |
|---|---|
| `run_shortcut(name, input?)` | `shortcuts run "<name>" --input-path … --output-path …` (returns the shortcut's text output) |
| `list_shortcuts()` | `shortcuts list` |
| `run_applescript(script)` | `osascript` |
| `open_app(name)` | `open -a` |
| `set_volume(level)` | `set volume output volume` |
| `set_brightness(level)` | the `brightness` CLI if installed (`brew install brightness`), otherwise simulated brightness keys (16 steps) |
| `get_battery_status()` | `pmset -g batt` |
| `control_music(action)` | Music app via AppleScript: play, pause, next, previous, stop, now_playing |
| `send_imessage(recipient, body)` | Messages via AppleScript. A contact name is looked up in Contacts. |
| `create_reminder(text, when?)` | Reminders via AppleScript. Syncs to iPhone. |
| `create_calendar_event(title, start, end?)` | Calendar via AppleScript. Syncs to iPhone. |
| `web_search_or_open(query)` | Opens a URL, or a Google search in your default browser. |
| `get_datetime()` | Local Python clock. |

To add a tool, write the function in `tools.py`, add it to `TOOL_FUNCTIONS`, and add a `_schema(...)` entry to `TOOL_SCHEMAS`.

## Configuration (`.env`)

| Variable | Default | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | (none) | Required. |
| `PICOVOICE_ACCESS_KEY` | (none) | Optional. Without it, the wake word is openWakeWord's "Hey Jarvis". |
| `JARVIS_MODEL` | `claude-sonnet-5-5` | |
| `JARVIS_EFFORT` | `low` | `low`, `medium` or `high`. Higher is smarter but slower. |
| `WHISPER_MODEL` | `base.en` | `small.en` is more accurate and takes roughly 2–3× longer. |
| `WAKE_SENSITIVITY` | `0.6` | Raise it if Jarvis misses you; lower it if it triggers by accident. |
| `SILENCE_SECONDS` | `1.0` | Pause length that ends a command. |
| `MAX_RECORD_SECONDS` | `15` | |
| `TTS_ENGINE` | `say` | `elevenlabs` also needs `ELEVENLABS_API_KEY` and `ELEVENLABS_VOICE_ID` (cloud). |
| `TTS_VOICE` / `TTS_RATE` | `Daniel` / `185` | |
| `JARVIS_CONFIRM_APPLESCRIPT` | `risky` | `risky`, `always` or `never`. |

**Swapping the voice.** `tts.py` has a `Speaker` base class with a single `speak(text)` method. Subclass it and return your engine from `make_speaker()`. ElevenLabs is already wired up as an example.

## Troubleshooting

- **Nothing happens when I say "Jarvis".**
  - Check the Microphone permission for your terminal.
  - Try `--list-devices` and `--device N`.
  - Raise `WAKE_SENSITIVITY`.
  - Without a Picovoice key, the phrase is "Hey Jarvis".
- **It cuts me off, or never stops recording.** Adjust `SILENCE_SECONDS`. Background noise is measured while Jarvis waits for the wake word, so a fan or music in the room shifts the threshold.
- **Jarvis hears itself.** The mic is paused while Jarvis speaks. If you're on speakers with no headphones, keep the volume moderate.
- **`Not authorized to send Apple events`.** Automation permission is missing. See [macOS permissions](#macos-permissions).
- **Shortcut not found.** The name must match exactly, including capitalisation. Check with `shortcuts list`.
