"""Platform-independent tests: transcript cleaning, confirmations, the Claude tool loop (mocked)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anthropic  # noqa: E402
import pytest  # noqa: E402

import jarvis  # noqa: E402
import tools  # noqa: E402

try:
    import httpx2 as httpx  # anthropic >= 2
except ImportError:  # pragma: no cover
    import httpx


def test_clean_transcript():
    assert jarvis.clean_transcript("Hey Jarvis, what's my battery?") == "what's my battery?"
    assert jarvis.clean_transcript("Jarvis open Safari") == "open Safari"
    assert jarvis.clean_transcript(" Thank you.") == ""


@pytest.mark.parametrize("text,expected", [
    ("Yes please", True), ("yeah go ahead", True), ("no", False), ("no, don't", False), ("hmm", False)])
def test_is_yes(text, expected):
    assert jarvis.is_yes(text) is expected


def test_schemas_match_functions():
    assert {t["name"] for t in tools.TOOL_SCHEMAS} == set(tools.TOOL_FUNCTIONS)


def test_imessage_requires_confirmation():
    asked = []
    r = tools.execute_tool("send_imessage", {"recipient": "Pepper", "body": "hi"},
                           confirm=lambda q: asked.append(q) or False)
    assert asked and not r["ok"] and "NOT performed" in r["error"]


def test_risky_applescript_detection():
    assert tools.confirmation_prompt("run_applescript", {"script": 'tell app "Finder" to delete file "x"'})
    assert tools.confirmation_prompt("run_applescript", {"script": 'tell app "Music" to get name'}) is None
    assert tools.confirmation_prompt("run_shortcut", {"name": "Jarvis Text"})
    assert tools.confirmation_prompt("run_shortcut", {"name": "Jarvis Weather"}) is None


def test_unknown_tool_and_bad_args():
    assert not tools.execute_tool("nope", {})["ok"]
    assert not tools.execute_tool("set_volume", {"wrong": 1})["ok"]


def _brain(handler):
    b = jarvis.Brain()
    b.client = anthropic.Anthropic(api_key="test", max_retries=0,
                                   http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    return b


def test_tool_loop_end_to_end():
    calls = []
    base = {"id": "m", "type": "message", "role": "assistant", "model": jarvis.MODEL,
            "usage": {"input_tokens": 1, "output_tokens": 1}}

    def handler(req):
        calls.append(json.loads(req.content))
        if len(calls) == 1:
            return httpx.Response(200, json={**base, "stop_reason": "tool_use", "content": [
                {"type": "tool_use", "id": "t1", "name": "get_datetime", "input": {}}]})
        return httpx.Response(200, json={**base, "stop_reason": "end_turn",
                                         "content": [{"type": "text", "text": "Seven o'clock, sir."}]})

    b = _brain(handler)
    assert b.respond("time?", lambda q: True) == "Seven o'clock, sir."
    body = calls[1]
    assert body["model"] == jarvis.MODEL and body["output_config"] == {"effort": "low"}
    assert body["messages"][2]["content"][0]["type"] == "tool_result"
    assert json.loads(body["messages"][2]["content"][0]["content"])["ok"] is True
    assert len(b.history) == 2


def test_errors_are_spoken_not_raised():
    b = _brain(lambda r: httpx.Response(401, json={"type": "error", "error": {"type": "authentication_error",
                                                                              "message": "bad"}}))
    assert "rejected" in b.respond("hi", lambda q: True)
    b = jarvis.Brain()
    b.client = None
    assert "no Anthropic API key" in b.respond("hi", lambda q: True)
