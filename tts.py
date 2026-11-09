"""
Text-to-speech. Swap engines with TTS_ENGINE in .env:
  say         (default) macOS built-in `say`, voice "Daniel" (British). Fully local.
  elevenlabs  ElevenLabs API (cloud). Needs ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID.

To add another engine, subclass Speaker and register it in make_speaker().
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request


class Speaker:
    def speak(self, text: str) -> None:
        raise NotImplementedError


class PrintSpeaker(Speaker):
    """Fallback when no audio output engine is available (e.g. not on macOS)."""

    def speak(self, text: str) -> None:
        print(f"[speech] {text}")


class MacSaySpeaker(Speaker):
    def __init__(self, voice: str = "Daniel", rate: int = 185):
        self.voice = voice if self._voice_installed(voice) else None
        if self.voice is None:
            print(f"[tts] Voice '{voice}' not installed; using system default. "
                  "Add it in System Settings → Accessibility → Spoken Content → System Voice → Manage Voices.")
        self.rate = rate

    @staticmethod
    def _voice_installed(voice: str) -> bool:
        try:
            out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True, timeout=10).stdout
        except Exception:
            return False
        return any(line.split()[0] == voice for line in out.splitlines() if line.strip())

    def speak(self, text: str) -> None:
        cmd = ["say", "-r", str(self.rate)]
        if self.voice:
            cmd += ["-v", self.voice]
        try:
            subprocess.run(cmd + [text], check=False, timeout=120)
        except Exception as e:
            print(f"[tts] say failed: {e}")


class ElevenLabsSpeaker(Speaker):
    URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"

    def __init__(self, api_key: str, voice_id: str, fallback: Speaker):
        self.api_key, self.voice_id, self.fallback = api_key, voice_id, fallback

    def speak(self, text: str) -> None:
        body = json.dumps({"text": text, "model_id": "eleven_turbo_v2_5"}).encode()
        req = urllib.request.Request(
            self.URL.format(voice_id=self.voice_id), data=body, method="POST",
            headers={"xi-api-key": self.api_key, "Content-Type": "application/json", "Accept": "audio/mpeg"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r, \
                    tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                f.write(r.read())
                path = f.name
            subprocess.run(["afplay", path], check=False)
            os.unlink(path)
        except Exception as e:
            print(f"[tts] ElevenLabs failed ({e}); falling back.")
            self.fallback.speak(text)


def make_speaker() -> Speaker:
    if sys.platform != "darwin" or not shutil.which("say"):
        return PrintSpeaker()
    say = MacSaySpeaker(os.getenv("TTS_VOICE", "Daniel"), int(os.getenv("TTS_RATE", "185")))
    if os.getenv("TTS_ENGINE", "say").lower() == "elevenlabs":
        key, voice = os.getenv("ELEVENLABS_API_KEY"), os.getenv("ELEVENLABS_VOICE_ID")
        if key and voice:
            return ElevenLabsSpeaker(key, voice, fallback=say)
        print("[tts] TTS_ENGINE=elevenlabs but ELEVENLABS_API_KEY/ELEVENLABS_VOICE_ID missing; using say.")
    return say
