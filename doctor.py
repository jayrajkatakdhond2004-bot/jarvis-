#!/usr/bin/env python3
"""
Jarvis self-check. Verifies every piece on this Mac and says exactly how to fix what's broken.

    python doctor.py                  # full check (records 2 s from the mic)
    python doctor.py --quick          # skip microphone + Whisper
    python doctor.py --open-settings  # also open the System Settings panes that need attention
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

RECOMMENDED_SHORTCUTS = [
    "Jarvis Text", "Jarvis Set Timer", "Jarvis Play Playlist", "Jarvis Find My iPhone", "Jarvis Focus",
    "Jarvis Note", "Jarvis Home Scene", "Jarvis Directions", "Jarvis Weather", "Jarvis Call", "Jarvis Phone",
]
SETTINGS = {
    "microphone": "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone",
    "accessibility": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
    "automation": "x-apple.systempreferences:com.apple.preference.security?Privacy_Automation",
}

results: list[tuple[str, str, str]] = []  # (status, name, detail)
to_open: set[str] = set()


def ok(name, detail=""):
    results.append(("✅", name, detail))


def warn(name, detail):
    results.append(("⚠️ ", name, detail))


def fail(name, detail):
    results.append(("❌", name, detail))


def osa(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=20)


# --------------------------------------------------------------------------- checks


def check_system():
    if sys.version_info >= (3, 11):
        ok("Python", platform.python_version())
    else:
        fail("Python", f"{platform.python_version()} — need 3.11+. Run ./setup.sh")
    if sys.platform != "darwin":
        fail("macOS", "Not running on macOS; tools and voice won't work.")
        return False
    arch = platform.machine()
    (ok if arch == "arm64" else warn)("Apple Silicon", arch if arch == "arm64" else
                                      f"{arch} — works, but Whisper will be slower (or Python is running under Rosetta).")
    return True


def check_packages():
    missing = []
    for mod in ("anthropic", "numpy", "sounddevice", "faster_whisper", "pvporcupine", "openwakeword"):
        try:
            __import__(mod)
        except Exception as e:
            missing.append(f"{mod} ({type(e).__name__})")
    if missing:
        fail("Python packages", "Missing: " + ", ".join(missing) + ". Run: pip install -r requirements.txt")
    else:
        ok("Python packages")


def check_anthropic():
    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not key:
        fail("Anthropic API key", "ANTHROPIC_API_KEY is empty in .env")
        return
    import anthropic

    model = os.getenv("JARVIS_MODEL", "claude-sonnet-5-5")
    try:
        anthropic.Anthropic(api_key=key, max_retries=1, timeout=20).models.retrieve(model)
        ok("Anthropic API key", f"valid, {model} available")
    except anthropic.AuthenticationError:
        fail("Anthropic API key", "Rejected — re-copy it from console.anthropic.com into .env")
    except anthropic.NotFoundError:
        fail("Anthropic API key", f"Key works but model {model} isn't available to it")
    except anthropic.APIConnectionError:
        warn("Anthropic API key", "Couldn't reach api.anthropic.com — check your internet connection")
    except Exception as e:
        warn("Anthropic API key", f"{type(e).__name__}: {e}")


def check_wake_word():
    key = os.getenv("PICOVOICE_ACCESS_KEY", "").strip()
    if key:
        try:
            import pvporcupine

            pvporcupine.create(access_key=key, keywords=["jarvis"]).delete()
            ok("Wake word", 'Porcupine — say "Jarvis"')
            return
        except Exception as e:
            warn("Wake word", f"Porcupine key failed ({str(e).splitlines()[0][:120]}); will use openWakeWord")
    try:
        from audio import OpenWakeWord

        OpenWakeWord(threshold=0.7)
        ok("Wake word", 'openWakeWord fallback — say "Hey Jarvis" (add PICOVOICE_ACCESS_KEY for plain "Jarvis")')
    except Exception as e:
        fail("Wake word", f"openWakeWord failed: {e}. Jarvis will fall back to push-to-talk (--ptt).")


def check_voice():
    out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout
    if any(line.split()[:1] == ["Daniel"] for line in out.splitlines()):
        ok("Daniel voice")
    else:
        warn("Daniel voice", "Not installed — System Settings → Accessibility → Spoken Content → "
                             "System Voice → Manage Voices → English (UK) → Daniel")


def check_microphone():
    try:
        import numpy as np
        import sounddevice as sd

        print("   (recording 2 seconds from the microphone — say something)…")
        rec = sd.rec(int(2 * 16000), samplerate=16000, channels=1, dtype="int16")
        sd.wait()
        peak = int(np.abs(rec).max())
        name = sd.query_devices(kind="input")["name"]
        if peak == 0:
            fail("Microphone", "Recorded pure silence — microphone permission is probably denied for this terminal app. "
                               "Allow it, then quit (⌘Q) and reopen the terminal.")
            to_open.add("microphone")
        elif peak < 300:
            warn("Microphone", f"'{name}' works but is very quiet (peak {peak}). Check the input level in Sound settings.")
        else:
            ok("Microphone", f"'{name}' (peak level {peak})")
    except Exception as e:
        fail("Microphone", f"{type(e).__name__}: {e}")
        to_open.add("microphone")


def check_whisper():
    try:
        import time

        import numpy as np

        from audio import Transcriber

        t = time.monotonic()
        stt = Transcriber()
        stt.transcribe(np.zeros(16000, dtype=np.float32))
        ok("Whisper", f"{stt.model_size} loaded in {time.monotonic() - t:.1f}s")
    except Exception as e:
        fail("Whisper", f"{type(e).__name__}: {e}")


def check_permissions():
    r = osa('tell application "System Events" to get UI elements enabled')
    if r.returncode == 0 and r.stdout.strip() == "true":
        ok("Accessibility")
    elif r.returncode == 0:
        warn("Accessibility", "Not granted — needed for brightness and keystroke scripts. Add your terminal app.")
        to_open.add("accessibility")
    else:
        warn("Automation → System Events", "Not allowed yet: " + r.stderr.strip()[:120])
        to_open.add("automation")

    for app in ("Music", "Reminders", "Calendar", "Messages", "Contacts"):
        r = osa(f'tell application "{app}" to get name')
        if r.returncode == 0:
            ok(f"Automation → {app}")
        elif "-1743" in r.stderr or "Not authorized" in r.stderr:
            warn(f"Automation → {app}", "Denied. Enable it under Privacy & Security → Automation → your terminal.")
            to_open.add("automation")
        else:
            warn(f"Automation → {app}", r.stderr.strip()[:120] or "unknown error")


def check_shortcuts():
    if not shutil.which("shortcuts"):
        warn("Shortcuts", "`shortcuts` command not found (needs macOS 12+)")
        return
    have = set(subprocess.run(["shortcuts", "list"], capture_output=True, text=True).stdout.splitlines())
    missing = [s for s in RECOMMENDED_SHORTCUTS if s not in have]
    if not missing:
        ok("Shortcuts", f"all {len(RECOMMENDED_SHORTCUTS)} recommended shortcuts found")
    else:
        found = len(RECOMMENDED_SHORTCUTS) - len(missing)
        warn("Shortcuts", f"{found}/{len(RECOMMENDED_SHORTCUTS)} created. Still to create (see RECOMMENDED_SHORTCUTS.md): "
             + ", ".join(missing))


def check_optional():
    if any(os.path.exists(p) for p in ("/opt/homebrew/bin/brightness", "/usr/local/bin/brightness")):
        ok("brightness CLI")
    else:
        warn("brightness CLI", "Optional: `brew install brightness` for precise brightness control")


# --------------------------------------------------------------------------- main


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="skip microphone and Whisper")
    ap.add_argument("--open-settings", action="store_true", help="open the System Settings panes that need fixing")
    args = ap.parse_args()

    print("Jarvis self-check\n")
    if check_system():
        check_packages()
        check_anthropic()
        check_wake_word()
        check_voice()
        if not args.quick:
            check_microphone()
            check_whisper()
        check_permissions()
        check_shortcuts()
        check_optional()

    width = max(len(n) for _, n, _ in results)
    for status, name, detail in results:
        print(f"{status} {name.ljust(width)}  {detail}")

    if args.open_settings and to_open and sys.platform == "darwin":
        for pane in sorted(to_open):
            subprocess.run(["open", SETTINGS[pane]])
        print("\nOpened the System Settings panes that need attention. "
              "After changing them, quit your terminal (⌘Q) and reopen it.")

    failures = sum(1 for s, _, _ in results if s == "❌")
    print(f"\n{'All essential checks passed.' if not failures else f'{failures} problem(s) to fix above.'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
