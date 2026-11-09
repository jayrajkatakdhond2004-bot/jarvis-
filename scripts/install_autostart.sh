#!/usr/bin/env bash
# Start Jarvis automatically at login (and restart it if it crashes).
# Logs: ~/Library/Logs/Jarvis.log      Remove with: ./scripts/uninstall_autostart.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/com.jarvis.assistant.plist"
PY="$ROOT/.venv/bin/python"
[[ -x "$PY" ]] || { echo "Run ./setup.sh first."; exit 1; }
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.jarvis.assistant</string>
  <key>ProgramArguments</key>
  <array><string>$PY</string><string>-u</string><string>$ROOT/jarvis.py</string></array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$HOME/Library/Logs/Jarvis.log</string>
  <key>StandardErrorPath</key><string>$HOME/Library/Logs/Jarvis.log</string>
  <key>ProcessType</key><string>Interactive</string>
</dict>
</plist>
PLISTEOF
launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Jarvis will now start at login. It's starting now too."
echo "Logs:  tail -f ~/Library/Logs/Jarvis.log"
echo "NOTE: run in the background, macOS asks for Microphone/Automation permission for Python itself"
echo "      (not Terminal). Approve those prompts, or add $PY under Privacy & Security."
