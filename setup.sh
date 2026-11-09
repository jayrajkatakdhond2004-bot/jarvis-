#!/usr/bin/env bash
# One-command setup for Jarvis on macOS:  ./setup.sh
# Creates the virtualenv, installs dependencies, asks for your API keys (stored only in .env),
# pre-downloads the speech models, then runs the self-check (doctor.py).
set -euo pipefail
cd "$(dirname "$0")"

bold() { printf "\033[1m%s\033[0m\n" "$*"; }
warn() { printf "\033[33m⚠️  %s\033[0m\n" "$*"; }

[[ "$(uname)" == "Darwin" ]] || warn "This isn't macOS — Jarvis's tools and voice need a Mac."

# 1. Python 3.11+
PY=""
for c in python3.13 python3.12 python3.11 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
    PY="$(command -v "$c")"; break
  fi
done
if [[ -z "$PY" ]]; then
  if command -v brew >/dev/null 2>&1; then
    bold "Installing Python 3.12 with Homebrew…"
    brew install python@3.12
    PY="$(brew --prefix)/bin/python3.12"
  else
    echo "Python 3.11+ not found. Install Homebrew (https://brew.sh) then re-run ./setup.sh"; exit 1
  fi
fi
bold "Using $("$PY" --version) at $PY"

# 2. Virtualenv + dependencies
[[ -d .venv ]] || "$PY" -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
bold "Installing dependencies (a few minutes the first time)…"
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

# 3. API keys → .env (never committed)
[[ -f .env ]] || cp .env.example .env
set_env() {  # set_env NAME VALUE — replaces the NAME= line in .env
  python - "$1" "$2" <<'PYEOF'
import re, sys, pathlib
name, value = sys.argv[1], sys.argv[2]
p = pathlib.Path(".env"); s = p.read_text()
line = f"{name}={value}"
s = re.sub(rf"^#?\s*{name}=.*$", line, s, count=1, flags=re.M) if re.search(rf"^#?\s*{name}=", s, re.M) else s + "\n" + line + "\n"
p.write_text(s)
PYEOF
}
get_env() { grep -E "^$1=" .env | head -1 | cut -d= -f2- || true; }

if [[ -z "$(get_env ANTHROPIC_API_KEY)" ]]; then
  echo
  bold "Anthropic API key (https://console.anthropic.com/settings/keys) — input is hidden:"
  read -rs KEY; echo
  [[ -n "$KEY" ]] && set_env ANTHROPIC_API_KEY "$KEY" || warn "Skipped — Jarvis will tell you the key is missing."
fi
if [[ -z "$(get_env PICOVOICE_ACCESS_KEY)" ]]; then
  echo
  bold "Picovoice AccessKey for the \"Jarvis\" wake word (free at https://console.picovoice.ai/)."
  echo "Press Enter to skip and use the free \"Hey Jarvis\" fallback instead. Input is hidden:"
  read -rs PV; echo
  [[ -n "$PV" ]] && set_env PICOVOICE_ACCESS_KEY "$PV"
fi
chmod 600 .env

# 4. Pre-download models so the first real run is instant
bold "Downloading speech models…"
python - <<'PYEOF'
from dotenv import load_dotenv; load_dotenv(".env")
from audio import Transcriber
Transcriber(); print("  ✓ Whisper ready")
try:
    import openwakeword
    openwakeword.utils.download_models(model_names=["hey_jarvis"]); print("  ✓ openWakeWord fallback ready")
except Exception as e:
    print(f"  ! openWakeWord download failed: {e}")
PYEOF

# 5. Self-check
echo
bold "Running self-check…"
python doctor.py --open-settings || true

echo
bold "Done. Start Jarvis with:"
echo "  source .venv/bin/activate && python jarvis.py"
echo "Optional: start automatically at login with ./scripts/install_autostart.sh"
