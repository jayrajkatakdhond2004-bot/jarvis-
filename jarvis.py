#!/usr/bin/env python3
"""
Jarvis — a local voice assistant for macOS.

Loop: wake word → record until silence → local Whisper → Claude (with tools) → speak → repeat.

    python jarvis.py              # voice mode (wake word)
    python jarvis.py --ptt        # push-to-talk: press Enter instead of saying "Jarvis"
    python jarvis.py --text       # type instead of talk (no microphone needed)
    python jarvis.py --list-devices
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import deque
from pathlib import Path
from typing import Callable

from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"))

import anthropic  # noqa: E402

from tools import TOOL_SCHEMAS, execute_tool  # noqa: E402
from tts import Speaker, make_speaker  # noqa: E402

MODEL = os.getenv("JARVIS_MODEL", "claude-sonnet-5-5")
EFFORT = os.getenv("JARVIS_EFFORT", "low")
MAX_TURNS = 10          # rolling history: last N user/assistant exchanges
MAX_TOOL_STEPS = 8      # safety cap on tool-call rounds per request
FALLBACK_BETA = "server-side-fallback-2026-07-01"

SYSTEM_PROMPT = """You are J.A.R.V.I.S., the user's personal AI assistant, running on their Mac. \
You speak with a dry British wit: calm, efficient, lightly sardonic, never fawning. Address the user as "sir".

Everything you write is read aloud by a text-to-speech voice, so:
- Keep replies to one to three short sentences unless the user asks for detail.
- Plain spoken English only: no markdown, bullet points, emoji, code, or URLs read out character by character.
- Say numbers and times the way a person would ("half past three", "eighty-two percent").

You can act on the user's Mac, and on their iPhone and iPad through Apple Shortcuts, using your tools. \
Use them whenever the request calls for an action or for live information (time, battery, now playing). \
For iPhone/iPad actions (texts from the phone, timers, Find My, Focus, HomeKit scenes), use run_shortcut; \
if you're not sure a shortcut exists, call list_shortcuts. For relative dates like "tomorrow at nine", \
call get_datetime first, then pass ISO 8601 local times.

Honesty about actions matters more than anything else:
- Only say you did something after the tool returned "ok": true.
- If a tool returns an error, tell the user briefly what went wrong and, if useful, how to fix it \
(for example a missing macOS permission). Never pretend it worked.
- If the user declined a confirmation, acknowledge it; do not retry.
- Risky actions (sending messages, running scripts that change things) are confirmed with the user \
automatically by the system before they run, so just call the tool; don't ask for confirmation yourself.
"""


# --------------------------------------------------------------------------- brain


class Brain:
    """Claude with tool use and a rolling conversation history."""

    def __init__(self) -> None:
        key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        self.client = anthropic.Anthropic(api_key=key, timeout=60, max_retries=2) if key else None
        self.history: deque[dict] = deque(maxlen=MAX_TURNS * 2)
        self.use_fallbacks = True

    @property
    def ready(self) -> bool:
        return self.client is not None

    def _create(self, messages: list[dict]):
        kwargs = dict(
            model=MODEL,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=TOOL_SCHEMAS,
            messages=messages,
            output_config={"effort": EFFORT},
            cache_control={"type": "ephemeral"},  # cache tools+system+history between turns
        )
        if self.use_fallbacks:
            # If a safety classifier declines, the API re-runs the request on a fallback model.
            try:
                return self.client.beta.messages.create(**kwargs, betas=[FALLBACK_BETA], fallbacks="default")
            except anthropic.BadRequestError as e:
                if "fallback" not in str(e).lower():
                    raise
                print("[brain] Server-side fallbacks unavailable for this account; continuing without them.")
                self.use_fallbacks = False
        return self.client.messages.create(**kwargs)

    def respond(self, text: str, confirm: Callable[[str], bool]) -> str:
        if not self.ready:
            return ("I'm afraid I have no Anthropic API key, sir. Add ANTHROPIC_API_KEY to the dot env file "
                    "and restart me.")
        # Earlier turns are kept as plain text; the current turn's tool loop is appended to unchanged.
        messages: list[dict] = list(self.history) + [{"role": "user", "content": text}]
        try:
            reply = self._run_loop(messages, confirm)
        except anthropic.AuthenticationError:
            return "My Anthropic API key was rejected, sir. Please check it in the dot env file."
        except anthropic.PermissionDeniedError:
            return f"My API key isn't permitted to use {MODEL}, sir."
        except anthropic.RateLimitError:
            return "I'm being rate limited, sir. Give me a moment and try again."
        except anthropic.APIConnectionError:
            return "I can't reach Anthropic's servers, sir. Is the internet connection up?"
        except anthropic.APIStatusError as e:
            print(f"[brain] API error {e.status_code}: {e.message}")
            return f"The API returned an error, status {e.status_code}, sir. Details are in the console."
        except Exception as e:  # never crash the main loop
            print(f"[brain] Unexpected error: {type(e).__name__}: {e}")
            return "Something went wrong on my end, sir. Details are in the console."

        self.history.append({"role": "user", "content": text})
        self.history.append({"role": "assistant", "content": reply})
        return reply

    def _run_loop(self, messages: list[dict], confirm: Callable[[str], bool]) -> str:
        for _ in range(MAX_TOOL_STEPS):
            resp = self._create(messages)

            if resp.stop_reason == "refusal":
                return "I'm afraid that's not something I can help with, sir."

            tool_uses = [b for b in resp.content if b.type == "tool_use"]
            if resp.stop_reason == "tool_use" and tool_uses:
                messages.append({"role": "assistant", "content": resp.content})  # pass back unchanged
                results = []
                for tu in tool_uses:
                    args = dict(tu.input or {})
                    print(f"  → {tu.name}({json.dumps(args)[:200]})")
                    result = execute_tool(tu.name, args, confirm=confirm)
                    print(f"  ← {json.dumps(result)[:300]}")
                    results.append({
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": json.dumps(result),
                        "is_error": not result.get("ok", False),
                    })
                messages.append({"role": "user", "content": results})  # all results in one message
                continue

            reply = " ".join(b.text for b in resp.content if b.type == "text").strip()
            return reply or "Done, sir."
        return "That was taking rather too many steps, sir, so I stopped. Shall we try it more simply?"


# --------------------------------------------------------------------------- text helpers

_WAKE_PREFIX = re.compile(r"^\s*(hey|okay|ok)?[\s,]*jarvis\b[\s,.!?:;-]*", re.I)
_HALLUCINATIONS = {"", "you", "thank you", "thanks for watching", "bye", "thank you for watching", "okay"}
_EXIT = re.compile(r"^(that will be all|that's all|stand down|goodbye|good night|go to sleep)\b", re.I)
_YES = re.compile(r"\b(yes|yeah|yep|yup|sure|affirmative|do it|go ahead|confirm(ed)?|please do|send it|proceed|correct)\b", re.I)
_NO = re.compile(r"\b(no|nope|don't|do not|cancel|stop|negative|wait|never mind)\b", re.I)


def clean_transcript(text: str) -> str:
    text = _WAKE_PREFIX.sub("", text).strip()
    return "" if text.lower().strip(" .!?,") in _HALLUCINATIONS else text


def is_yes(answer: str) -> bool:
    return bool(_YES.search(answer)) and not _NO.search(answer)


# --------------------------------------------------------------------------- modes


def speak(speaker: Speaker, text: str) -> None:
    print(f"Jarvis: {text}")
    speaker.speak(text)


def text_mode(brain: Brain, speaker: Speaker) -> None:
    def confirm(question: str) -> bool:
        speak(speaker, question)
        return is_yes(input("  (yes/no) > "))

    print("Text mode. Type a command (Ctrl-C to quit).")
    while True:
        try:
            text = input("\nYou: ").strip()
        except EOFError:
            return
        if not text:
            continue
        if _EXIT.match(text):
            speak(speaker, "Very good, sir. Standing down.")
            return
        speak(speaker, brain.respond(text, confirm))


def voice_mode(brain: Brain, speaker: Speaker, push_to_talk: bool, device) -> None:
    from audio import Microphone, Transcriber, chime, make_wake_word, record_until_silence, _rms

    print(f"Loading Whisper model '{os.getenv('WHISPER_MODEL', 'base.en')}' (first run downloads it)…")
    stt = Transcriber()
    try:
        mic = Microphone(device)
    except Exception as e:
        speak(speaker, "I can't open the microphone, sir. Check the microphone permission for your terminal.")
        print(f"[audio] {e}")
        sys.exit(1)
    wake = None if push_to_talk else make_wake_word()
    noise_levels: deque[float] = deque(maxlen=60)  # ~2 s of ambient level, measured while waiting

    def listen() -> str:
        audio = record_until_silence(mic, noise_floor=_median(noise_levels))
        mic.stop()
        if audio is None:
            return ""
        return clean_transcript(stt.transcribe(audio))

    def confirm(question: str) -> bool:
        speak(speaker, question)
        chime("listen")
        answer = listen()
        print(f"  (heard: {answer!r})")
        return is_yes(answer)

    if brain.ready:
        speak(speaker, "Jarvis online. At your service, sir.")
    else:
        speak(speaker, "Jarvis online, sir, though without an Anthropic API key I can't do much. "
                       "Please add it to the dot env file.")

    trigger = "press Enter" if wake is None else f"say {'“Jarvis”' if 'Porcupine' in wake.name else '“Hey Jarvis”'}"
    while True:
        print(f"\n🎙  Listening… ({trigger}; Ctrl-C to quit)" + (f"  [{wake.name}]" if wake else ""))
        if wake is None:
            input()
        else:
            wake.reset()
            mic.start()
            while True:
                frame = mic.read()
                noise_levels.append(_rms(frame))
                if wake.process(frame):
                    break
        chime("listen")
        print("   Wake word detected — go ahead, sir.")
        text = listen()
        if not text:
            print("   (didn't catch anything)")
            continue
        print(f"You: {text}")
        if _EXIT.match(text):
            speak(speaker, "Very good, sir. Standing down.")
            break
        speak(speaker, brain.respond(text, confirm))

    mic.close()
    if wake:
        wake.close()


def _median(values) -> float | None:
    if len(values) < 10:
        return None
    s = sorted(values)
    return s[len(s) // 2]


def main() -> None:
    ap = argparse.ArgumentParser(description="Jarvis voice assistant")
    ap.add_argument("--text", action="store_true", help="type commands instead of speaking")
    ap.add_argument("--ptt", action="store_true", help="push-to-talk (Enter) instead of wake word")
    ap.add_argument("--device", default=None, help="input device index or name (see --list-devices)")
    ap.add_argument("--list-devices", action="store_true", help="list audio devices and exit")
    args = ap.parse_args()

    if args.list_devices:
        import sounddevice as sd
        print(sd.query_devices())
        return

    if sys.platform != "darwin":
        print("[warn] Not running on macOS: tools and speech will be unavailable or simulated.")
    speaker = make_speaker()
    brain = Brain()
    if not brain.ready:
        print("[warn] ANTHROPIC_API_KEY is not set — copy .env.example to .env and fill it in.")

    device = int(args.device) if args.device and args.device.isdigit() else args.device
    try:
        if args.text:
            text_mode(brain, speaker)
        else:
            voice_mode(brain, speaker, args.ptt, device)
    except KeyboardInterrupt:
        print("\nJarvis: Very good, sir.")


if __name__ == "__main__":
    main()
