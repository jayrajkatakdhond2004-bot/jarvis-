"""
Jarvis's "hands": every tool Claude can call, plus the Anthropic tool schemas.

Every tool returns a dict: {"ok": True, ...} on success or {"ok": False, "error": "..."}.
Claude is told to only claim success when "ok" is True.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.parse
from typing import Any, Callable

IS_MAC = sys.platform == "darwin"


# --------------------------------------------------------------------------- helpers


def _ok(**kw: Any) -> dict:
    return {"ok": True, **kw}


def _err(msg: str) -> dict:
    return {"ok": False, "error": msg}


def _require_mac() -> dict | None:
    if not IS_MAC:
        return _err("This tool only works on macOS.")
    return None


def _run(cmd: list[str], timeout: float = 30, input_text: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, input=input_text)


def as_string(s: str) -> str:
    """Escape a Python string as an AppleScript string literal (with quotes)."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def osascript(script: str, timeout: float = 30) -> dict:
    """Run AppleScript. Returns {"ok", "output"} or an error dict."""
    if (e := _require_mac()):
        return e
    try:
        p = _run(["osascript", "-"], timeout=timeout, input_text=script)
    except subprocess.TimeoutExpired:
        return _err(f"AppleScript timed out after {timeout:.0f}s.")
    if p.returncode != 0:
        msg = (p.stderr or p.stdout).strip()
        if "-1743" in msg or "Not authorized" in msg:
            msg += " (Automation permission missing: System Settings → Privacy & Security → Automation.)"
        elif "-1719" in msg or "assistive access" in msg:
            msg += " (Accessibility permission missing: System Settings → Privacy & Security → Accessibility.)"
        return _err(msg or f"osascript exited with {p.returncode}")
    return _ok(output=p.stdout.strip())


def _applescript_date(var: str, iso: str) -> str:
    """AppleScript that builds a date in `var` from an ISO-8601 string, locale-independently."""
    d = _parse_iso(iso)
    secs = d.hour * 3600 + d.minute * 60 + d.second
    return (
        f"set {var} to current date\n"
        f"set day of {var} to 1\n"
        f"set year of {var} to {d.year}\n"
        f"set month of {var} to {d.month}\n"
        f"set day of {var} to {d.day}\n"
        f"set time of {var} to {secs}\n"
    )


def _parse_iso(s: str) -> _dt.datetime:
    s = s.strip().replace("Z", "+00:00")
    d = _dt.datetime.fromisoformat(s)
    if d.tzinfo is not None:  # convert to local wall-clock time
        d = d.astimezone().replace(tzinfo=None)
    return d


# --------------------------------------------------------------------------- tools


def run_shortcut(name: str, input: str | None = None) -> dict:
    """Run an Apple Shortcut by name (synced via iCloud → reaches iPhone/iPad actions)."""
    if (e := _require_mac()):
        return e
    with tempfile.TemporaryDirectory() as tmp:
        out_path = os.path.join(tmp, "out")
        cmd = ["shortcuts", "run", name, "--output-path", out_path]
        if input:
            in_path = os.path.join(tmp, "in.txt")
            with open(in_path, "w") as f:
                f.write(input)
            cmd += ["--input-path", in_path]
        try:
            p = _run(cmd, timeout=90)
        except subprocess.TimeoutExpired:
            return _err(f"Shortcut '{name}' timed out.")
        if p.returncode != 0:
            msg = (p.stderr or p.stdout).strip() or f"exit code {p.returncode}"
            if "couldn't find" in msg.lower() or "not found" in msg.lower():
                msg = f"No shortcut named '{name}' exists. " + msg
            return _err(msg)
        output = ""
        if os.path.exists(out_path):
            try:
                with open(out_path, "r", errors="replace") as f:
                    output = f.read(4000).strip()
            except OSError:
                pass
        return _ok(shortcut=name, output=output or "(no output)")


def list_shortcuts() -> dict:
    if (e := _require_mac()):
        return e
    p = _run(["shortcuts", "list"], timeout=20)
    if p.returncode != 0:
        return _err(p.stderr.strip() or "Could not list shortcuts.")
    names = [n for n in p.stdout.splitlines() if n.strip()]
    return _ok(shortcuts=names)


def run_applescript(script: str) -> dict:
    return osascript(script, timeout=60)


def open_app(name: str) -> dict:
    if (e := _require_mac()):
        return e
    p = _run(["open", "-a", name], timeout=20)
    if p.returncode != 0:
        return _err(p.stderr.strip() or f"Could not open '{name}'.")
    return _ok(opened=name)


def set_volume(level: int) -> dict:
    level = max(0, min(100, int(level)))
    r = osascript(f"set volume output volume {level}" + (" without output muted" if level > 0 else ""))
    return _ok(volume=level) if r["ok"] else r


def set_brightness(level: int) -> dict:
    """Set display brightness 0-100. Uses the `brightness` CLI if installed, else simulated key presses."""
    if (e := _require_mac()):
        return e
    level = max(0, min(100, int(level)))
    for exe in ("/opt/homebrew/bin/brightness", "/usr/local/bin/brightness"):
        if os.path.exists(exe):
            p = _run([exe, f"{level / 100:.2f}"], timeout=10)
            if p.returncode == 0:
                return _ok(brightness=level, method="brightness CLI")
    # Fallback: 16 brightness steps on macOS. Drop to zero, then step up.
    steps = round(level / 100 * 16)
    script = (
        'tell application "System Events"\n'
        "repeat 16 times\nkey code 145\ndelay 0.02\nend repeat\n"
        f"repeat {steps} times\nkey code 144\ndelay 0.02\nend repeat\n"
        "end tell"
    )
    r = osascript(script)
    if not r["ok"]:
        return _err(r["error"] + " Tip: `brew install brightness` gives precise control.")
    return _ok(brightness=level, method="approximate (key presses, 16 steps)")


def get_battery_status() -> dict:
    if (e := _require_mac()):
        return e
    p = _run(["pmset", "-g", "batt"], timeout=10)
    if p.returncode != 0:
        return _err(p.stderr.strip() or "pmset failed")
    out = p.stdout
    m = re.search(r"(\d+)%;\s*([^;]+);\s*([^\n]*)", out)
    if not m:
        return _ok(has_battery=False, raw=out.strip(), note="No battery found (desktop Mac?).")
    source = "AC power" if "AC Power" in out else "battery"
    remaining = m.group(3).split("present")[0].strip()
    return _ok(has_battery=True, percent=int(m.group(1)), state=m.group(2).strip(),
               remaining=remaining or "unknown", power_source=source)


def control_music(action: str) -> dict:
    action = action.lower().strip()
    commands = {
        "play": "play",
        "pause": "pause",
        "playpause": "playpause",
        "toggle": "playpause",
        "next": "next track",
        "previous": "previous track",
        "stop": "stop",
    }
    if action == "now_playing":
        r = osascript(
            'tell application "Music"\n'
            'if player state is stopped then return "stopped"\n'
            'return (name of current track) & " — " & (artist of current track) & " [" & (player state as text) & "]"\n'
            "end tell"
        )
        return _ok(now_playing=r["output"]) if r["ok"] else r
    if action not in commands:
        return _err(f"Unknown action '{action}'. Use one of: {', '.join(list(commands) + ['now_playing'])}.")
    r = osascript(f'tell application "Music" to {commands[action]}')
    return _ok(action=action) if r["ok"] else r


def _resolve_contact(recipient: str) -> tuple[str | None, str]:
    """Turn a contact name into a phone/email. Returns (handle, display_name)."""
    if re.search(r"[\d@+]", recipient):
        return recipient, recipient
    script = (
        'tell application "Contacts"\n'
        f"set ppl to (every person whose name contains {as_string(recipient)})\n"
        'if (count of ppl) is 0 then return ""\n'
        "set p to item 1 of ppl\n"
        "set h to \"\"\n"
        "if (count of phones of p) > 0 then\n set h to value of item 1 of phones of p\n"
        "else if (count of emails of p) > 0 then\n set h to value of item 1 of emails of p\nend if\n"
        'return (name of p) & "|" & h\n'
        "end tell"
    )
    r = osascript(script)
    if not r["ok"] or "|" not in r.get("output", ""):
        return None, recipient
    name, handle = r["output"].split("|", 1)
    return (handle or None), name


def send_imessage(recipient: str, body: str) -> dict:
    if (e := _require_mac()):
        return e
    handle, display = _resolve_contact(recipient)
    if not handle:
        return _err(f"I couldn't find a phone number or email for '{recipient}' in Contacts.")
    script = (
        'tell application "Messages"\n'
        "set svc to 1st account whose service type = iMessage\n"
        f"set buddy to participant {as_string(handle)} of svc\n"
        f"send {as_string(body)} to buddy\n"
        "end tell"
    )
    r = osascript(script)
    return _ok(sent_to=display, handle=handle) if r["ok"] else r


def create_reminder(text: str, when: str | None = None, list_name: str | None = None) -> dict:
    try:
        date_script = _applescript_date("dueDate", when) if when else ""
    except ValueError:
        return _err(f"Could not parse date '{when}'. Use ISO 8601, e.g. 2026-11-10T09:00.")
    props = f"name:{as_string(text)}" + (", remind me date:dueDate" if when else "")
    target = f"list {as_string(list_name)}" if list_name else "default list"
    script = date_script + (
        'tell application "Reminders"\n'
        f"tell {target} to make new reminder with properties {{{props}}}\n"
        "end tell"
    )
    r = osascript(script)
    return _ok(reminder=text, when=when or "no due date") if r["ok"] else r


def create_calendar_event(title: str, start: str, end: str | None = None, calendar: str | None = None) -> dict:
    try:
        s = _parse_iso(start)
        e = _parse_iso(end) if end else s + _dt.timedelta(hours=1)
    except ValueError:
        return _err("Could not parse start/end. Use ISO 8601, e.g. 2026-11-10T14:00.")
    if e <= s:
        return _err("End time must be after start time.")
    cal = (f"calendar {as_string(calendar)}" if calendar
           else "(first calendar whose writable is true)")
    script = (
        _applescript_date("startDate", s.isoformat())
        + _applescript_date("endDate", e.isoformat())
        + 'tell application "Calendar"\n'
        f"tell {cal}\n"
        f"make new event with properties {{summary:{as_string(title)}, start date:startDate, end date:endDate}}\n"
        "end tell\nend tell"
    )
    r = osascript(script)
    return _ok(event=title, start=s.isoformat(timespec="minutes"), end=e.isoformat(timespec="minutes")) if r["ok"] else r


def web_search_or_open(query: str) -> dict:
    if (e := _require_mac()):
        return e
    q = query.strip()
    if re.match(r"^https?://", q):
        url = q
    elif re.match(r"^[\w-]+(\.[\w-]+)+(/\S*)?$", q):  # looks like a bare domain
        url = "https://" + q
    else:
        url = "https://www.google.com/search?q=" + urllib.parse.quote_plus(q)
    p = _run(["open", url], timeout=10)
    if p.returncode != 0:
        return _err(p.stderr.strip() or "Could not open browser.")
    return _ok(opened=url)


def get_datetime() -> dict:
    now = _dt.datetime.now().astimezone()
    return _ok(
        iso=now.isoformat(timespec="seconds"),
        spoken=now.strftime("%A %d %B %Y, %H:%M"),
        timezone=str(now.tzinfo),
    )


# --------------------------------------------------------------------------- registry + confirmation

TOOL_FUNCTIONS: dict[str, Callable[..., dict]] = {
    "run_shortcut": run_shortcut,
    "list_shortcuts": list_shortcuts,
    "run_applescript": run_applescript,
    "open_app": open_app,
    "set_volume": set_volume,
    "set_brightness": set_brightness,
    "get_battery_status": get_battery_status,
    "control_music": control_music,
    "send_imessage": send_imessage,
    "create_reminder": create_reminder,
    "create_calendar_event": create_calendar_event,
    "web_search_or_open": web_search_or_open,
    "get_datetime": get_datetime,
}

_RISKY_APPLESCRIPT = re.compile(
    r"\b(delete|remove|send|empty\s+trash|shut\s*down|restart|log\s*out|sleep|quit|close|erase|"
    r"do\s+shell\s+script|keystroke|key\s+code|move\b.*\bto\s+trash|rm\s)", re.I)
_RISKY_SHORTCUT = re.compile(r"(text|message|send|call|mail|email|delete|post|tweet|pay|lock|erase)", re.I)


def confirmation_prompt(name: str, args: dict) -> str | None:
    """Return a question to ask aloud before running this tool, or None if it's safe to just run."""
    if name == "send_imessage":
        return f"Shall I send {args.get('recipient')} the message: \"{args.get('body')}\"?"
    if name == "run_shortcut" and _RISKY_SHORTCUT.search(args.get("name", "") + " " + (args.get("input") or "")):
        extra = f" with \"{args['input']}\"" if args.get("input") else ""
        return f"Shall I run the shortcut {args.get('name')}{extra}?"
    if name == "run_applescript":
        mode = os.getenv("JARVIS_CONFIRM_APPLESCRIPT", "risky").lower()
        script = args.get("script", "")
        if mode == "always" or (mode == "risky" and _RISKY_APPLESCRIPT.search(script)):
            return "That requires running a script that could change or send things. Shall I proceed?"
    return None


def execute_tool(name: str, args: dict, confirm: Callable[[str], bool] | None = None) -> dict:
    """Run a tool by name. `confirm(question) -> bool` is called for risky actions."""
    fn = TOOL_FUNCTIONS.get(name)
    if fn is None:
        return _err(f"Unknown tool '{name}'.")
    question = confirmation_prompt(name, args)
    if question is not None:
        if confirm is None or not confirm(question):
            return _err("The user declined (or did not confirm) this action. It was NOT performed.")
    try:
        return fn(**args)
    except TypeError as e:
        return _err(f"Bad arguments for {name}: {e}")
    except subprocess.TimeoutExpired:
        return _err(f"{name} timed out.")
    except Exception as e:  # never let a tool crash the assistant
        return _err(f"{type(e).__name__}: {e}")


# --------------------------------------------------------------------------- Anthropic tool schemas


def _schema(name: str, description: str, props: dict | None = None, required: list[str] | None = None) -> dict:
    return {
        "name": name,
        "description": description,
        "input_schema": {
            "type": "object",
            "properties": props or {},
            "required": required or [],
            "additionalProperties": False,
        },
    }


ISO_HINT = "Local time as ISO 8601 without timezone, e.g. 2026-11-10T14:30. Call get_datetime first if you need today's date."

TOOL_SCHEMAS: list[dict] = [
    _schema(
        "run_shortcut",
        "Run an Apple Shortcut by exact name via the `shortcuts` CLI. This is the bridge to the user's "
        "iPhone/iPad (Shortcuts sync over iCloud) and to anything without a dedicated tool. Known shortcuts: "
        "'Jarvis Text', 'Jarvis Set Timer', 'Jarvis Play Playlist', 'Jarvis Find My iPhone', 'Jarvis Focus', "
        "'Jarvis Note', 'Jarvis Home Scene', 'Jarvis Directions', 'Jarvis Weather', 'Jarvis Call', and 'Jarvis Phone' (relays a command like 'TIMER 10' to run on the iPhone itself). "
        "If unsure a shortcut exists, call list_shortcuts first. Returns the shortcut's text output.",
        {"name": {"type": "string", "description": "Exact shortcut name."},
         "input": {"type": "string", "description": "Optional text passed as the shortcut's input."}},
        ["name"],
    ),
    _schema("list_shortcuts", "List the names of all Apple Shortcuts installed on this Mac."),
    _schema(
        "run_applescript",
        "Run arbitrary AppleScript via osascript for app/system control not covered by other tools "
        "(e.g. toggling dark mode, controlling Finder or Safari, reading the frontmost window). "
        "Prefer the dedicated tools when they fit. Returns the script's result as text.",
        {"script": {"type": "string", "description": "Complete AppleScript source."}},
        ["script"],
    ),
    _schema("open_app", "Open (launch or focus) a macOS application by name, e.g. 'Safari', 'Spotify'.",
            {"name": {"type": "string"}}, ["name"]),
    _schema("set_volume", "Set the Mac's output volume.",
            {"level": {"type": "integer", "minimum": 0, "maximum": 100}}, ["level"]),
    _schema("set_brightness", "Set the built-in display brightness.",
            {"level": {"type": "integer", "minimum": 0, "maximum": 100}}, ["level"]),
    _schema("get_battery_status", "Get battery percentage, charging state, time remaining and power source."),
    _schema("control_music", "Control the Apple Music app.",
            {"action": {"type": "string",
                        "enum": ["play", "pause", "playpause", "next", "previous", "stop", "now_playing"]}},
            ["action"]),
    _schema("send_imessage",
            "Send an iMessage from the Mac's Messages app. Recipient may be a contact name, phone number or email. "
            "The user will be asked to confirm aloud before it is sent.",
            {"recipient": {"type": "string"}, "body": {"type": "string"}}, ["recipient", "body"]),
    _schema("create_reminder", "Create a reminder in the Reminders app (syncs to iPhone/iPad).",
            {"text": {"type": "string"},
             "when": {"type": "string", "description": "Optional due/alert time. " + ISO_HINT},
             "list_name": {"type": "string", "description": "Optional Reminders list name."}},
            ["text"]),
    _schema("create_calendar_event", "Create an event in the Calendar app (syncs to iPhone/iPad).",
            {"title": {"type": "string"},
             "start": {"type": "string", "description": ISO_HINT},
             "end": {"type": "string", "description": "Optional; defaults to one hour after start. " + ISO_HINT},
             "calendar": {"type": "string", "description": "Optional calendar name."}},
            ["title", "start"]),
    _schema("web_search_or_open",
            "Open the default browser to a URL, or to a Google search if the query isn't a URL.",
            {"query": {"type": "string"}}, ["query"]),
    _schema("get_datetime", "Get the current local date, time and timezone."),
]


if __name__ == "__main__":  # quick manual test: python tools.py get_battery_status
    if len(sys.argv) < 2:
        print("Usage: python tools.py <tool_name> ['{\"json\": \"args\"}']")
        print("Tools:", ", ".join(TOOL_FUNCTIONS))
        sys.exit(0)
    a = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    print(json.dumps(execute_tool(sys.argv[1], a, confirm=lambda q: input(q + " [y/N] ").lower().startswith("y")), indent=2))
