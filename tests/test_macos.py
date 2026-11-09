"""Real-hardware tests: run only on macOS (GitHub Actions macos-14 = Apple Silicon)."""
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")

import tools  # noqa: E402


def synth(text, path):
    subprocess.run(["say", "-o", str(path), "--data-format=LEI16@16000", text], check=True)
    with wave.open(str(path)) as w:
        assert w.getframerate() == 16000
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    silence = np.zeros(8000, dtype=np.int16)
    return np.concatenate([silence, pcm, silence])


def test_basic_tools():
    assert tools.get_datetime()["ok"]
    assert tools.get_battery_status()["ok"]  # desktop/CI runners report has_battery=False
    assert tools.list_shortcuts()["ok"]
    assert tools.run_applescript('return 1 + 1') == {"ok": True, "output": "2"}


def test_whisper_transcribes_speech(tmp_path):
    from audio import Transcriber
    pcm = synth("What is my battery level?", tmp_path / "a.wav")
    text = Transcriber().transcribe(pcm.astype(np.float32) / 32768.0).lower()
    print("transcript:", text)
    assert "battery" in text


def test_openwakeword_detects_hey_jarvis(tmp_path):
    from audio import FRAME, OpenWakeWord
    pcm = synth("Hey Jarvis", tmp_path / "w.wav")
    ww = OpenWakeWord(threshold=0.5)
    hits = [ww.process(pcm[i:i + FRAME]) for i in range(0, len(pcm) - FRAME, FRAME)]
    assert any(hits)


def test_wake_word_ignores_other_speech(tmp_path):
    from audio import FRAME, OpenWakeWord
    pcm = synth("The weather is lovely today in London.", tmp_path / "n.wav")
    ww = OpenWakeWord(threshold=0.5)
    assert not any(ww.process(pcm[i:i + FRAME]) for i in range(0, len(pcm) - FRAME, FRAME))
