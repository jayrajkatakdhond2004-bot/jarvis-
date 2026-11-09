"""
Audio input: microphone, wake word detection, silence-terminated recording and local transcription.

Everything here runs locally. 16 kHz mono int16 throughout.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections import deque

import numpy as np

SAMPLE_RATE = 16000
FRAME = 512  # 32 ms — Porcupine's frame length at 16 kHz


# --------------------------------------------------------------------------- microphone


class Microphone:
    """Thin wrapper around a sounddevice input stream that yields int16 frames.

    The stream is stopped while Jarvis is thinking/speaking so it doesn't hear itself,
    and restarted (with a fresh buffer) when we listen again.
    """

    def __init__(self, device: int | str | None = None):
        import sounddevice as sd  # imported lazily so --text mode works without PortAudio

        self._sd = sd
        self.stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                                     blocksize=FRAME, device=device)
        self.active = False

    def start(self) -> None:
        if not self.active:
            self.stream.start()
            self.active = True

    def stop(self) -> None:
        if self.active:
            self.stream.stop()
            self.active = False

    def read(self) -> np.ndarray:
        self.start()
        data, _overflowed = self.stream.read(FRAME)
        return data[:, 0].copy()

    def close(self) -> None:
        self.stop()
        self.stream.close()


# --------------------------------------------------------------------------- wake word


class WakeWord:
    name = "base"

    def process(self, frame: np.ndarray) -> bool:
        raise NotImplementedError

    def reset(self) -> None:
        pass

    def close(self) -> None:
        pass


class PorcupineWakeWord(WakeWord):
    name = 'Porcupine ("Jarvis")'

    def __init__(self, access_key: str, sensitivity: float):
        import pvporcupine

        self.p = pvporcupine.create(access_key=access_key, keywords=["jarvis"], sensitivities=[sensitivity])
        assert self.p.frame_length == FRAME and self.p.sample_rate == SAMPLE_RATE

    def process(self, frame: np.ndarray) -> bool:
        return self.p.process(frame) >= 0

    def close(self) -> None:
        self.p.delete()


class OpenWakeWord(WakeWord):
    name = 'openWakeWord ("Hey Jarvis")'
    CHUNK = 1280  # 80 ms, what openWakeWord expects

    def __init__(self, threshold: float):
        import openwakeword
        from openwakeword.model import Model

        try:  # first run downloads the small pretrained models
            openwakeword.utils.download_models(model_names=["hey_jarvis"])
        except Exception as e:
            print(f"[wake] model download skipped/failed: {e}")
        self.model = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
        self.threshold = threshold
        self.buf = np.zeros(0, dtype=np.int16)

    def process(self, frame: np.ndarray) -> bool:
        self.buf = np.concatenate([self.buf, frame])
        hit = False
        while len(self.buf) >= self.CHUNK:
            chunk, self.buf = self.buf[: self.CHUNK], self.buf[self.CHUNK:]
            scores = self.model.predict(chunk)
            if any(v >= self.threshold for k, v in scores.items() if "jarvis" in k):
                hit = True
        return hit

    def reset(self) -> None:
        self.buf = np.zeros(0, dtype=np.int16)
        try:
            self.model.reset()
        except Exception:
            pass


def make_wake_word() -> WakeWord | None:
    """Porcupine if PICOVOICE_ACCESS_KEY is set, else openWakeWord, else None (push-to-talk)."""
    sens = float(os.getenv("WAKE_SENSITIVITY", "0.6"))
    key = os.getenv("PICOVOICE_ACCESS_KEY", "").strip()
    if key:
        try:
            return PorcupineWakeWord(key, sens)
        except Exception as e:
            print(f"[wake] Porcupine failed to start ({e}); falling back to openWakeWord.")
    try:
        return OpenWakeWord(threshold=1.0 - sens * 0.5)  # sensitivity 0.6 → threshold 0.7
    except Exception as e:
        print(f"[wake] openWakeWord failed to start ({e}); using push-to-talk (press Enter).")
    return None


# --------------------------------------------------------------------------- recording


def _rms(frame: np.ndarray) -> float:
    return float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))


def record_until_silence(mic: Microphone, noise_floor: float | None = None, silence_seconds: float | None = None,
                         max_seconds: float | None = None, start_timeout: float = 5.0) -> np.ndarray | None:
    """Record speech until the speaker stops. Returns float32 audio in [-1, 1], or None if nothing was said.

    Energy-based VAD with an adaptive noise floor: `noise_floor` is the ambient RMS measured while
    waiting for the wake word (if not given, we sample ~0.2 s now). Frames well above it count as speech.
    """
    silence_seconds = silence_seconds or float(os.getenv("SILENCE_SECONDS", "1.0"))
    max_seconds = max_seconds or float(os.getenv("MAX_RECORD_SECONDS", "15"))
    frame_s = FRAME / SAMPLE_RATE

    pre = [mic.read() for _ in range(3)]  # ~0.1 s, kept as pre-roll
    if noise_floor is None:
        noise_floor = float(np.median([_rms(f) for f in pre]))
    threshold = max(max(noise_floor, 50.0) * 2.5, 300.0)

    frames: list[np.ndarray] = list(pre)
    preroll: deque[np.ndarray] = deque(pre, maxlen=8)
    started = False
    silent_for = 0.0
    t0 = time.monotonic()

    while True:
        f = mic.read()
        loud = _rms(f) > threshold
        elapsed = time.monotonic() - t0
        if not started:
            preroll.append(f)
            if loud:
                started = True
                frames = list(preroll)
            elif elapsed > start_timeout:
                return None
            continue
        frames.append(f)
        silent_for = 0.0 if loud else silent_for + frame_s
        if silent_for >= silence_seconds or elapsed >= max_seconds:
            break

    audio = np.concatenate(frames).astype(np.float32) / 32768.0
    return audio if len(audio) > SAMPLE_RATE * 0.3 else None


def chime(kind: str = "listen") -> None:
    """Short non-blocking acknowledgement sound (macOS system sounds)."""
    if sys.platform != "darwin":
        return
    sound = {"listen": "Tink", "done": "Pop", "error": "Basso"}.get(kind, "Tink")
    try:
        subprocess.Popen(["afplay", f"/System/Library/Sounds/{sound}.aiff"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


# --------------------------------------------------------------------------- speech-to-text


class Transcriber:
    """faster-whisper on CPU with int8 — CTranslate2's fastest path on Apple Silicon (no Metal backend)."""

    def __init__(self, model_size: str | None = None):
        from faster_whisper import WhisperModel

        self.model_size = model_size or os.getenv("WHISPER_MODEL", "base.en")
        threads = max(4, (os.cpu_count() or 8) - 2)
        self.model = WhisperModel(self.model_size, device="cpu", compute_type="int8", cpu_threads=threads)

    def transcribe(self, audio: np.ndarray) -> str:
        lang = "en" if self.model_size.endswith(".en") else None
        segments, _ = self.model.transcribe(
            audio, language=lang, beam_size=1, vad_filter=True,
            condition_on_previous_text=False)
        return " ".join(s.text.strip() for s in segments).strip()
