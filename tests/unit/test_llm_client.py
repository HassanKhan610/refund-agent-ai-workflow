"""Live LLM client against a fake HTTP transport: no network, every edge case reproducible."""

import json

import httpx
import pytest

from app.agent.llm import ChatCompletionsClient, LLMError


def client_for(*responses, provider="openrouter"):
    calls = []
    urls = []
    queue = list(responses)

    def handler(request: httpx.Request):
        calls.append(json.loads(request.content))
        urls.append(str(request.url))
        r = queue.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    c = ChatCompletionsClient(
        provider,
        "key",
        "some/model:free",
        timeout=1,
        backoff_seconds=0,
        transport=httpx.MockTransport(handler),
    )
    c.urls = urls
    return c, calls


def ok(message):
    return httpx.Response(200, json={"choices": [{"message": message}]})


TOOL_MSG = {
    "content": None,
    "tool_calls": [
        {
            "id": "c1",
            "type": "function",
            "function": {"name": "lookup_order", "arguments": '{"order_id": "ORD-1001"}'},
        }
    ],
}


def test_parses_tool_calls_and_sends_tools_with_temperature_zero():
    c, calls = client_for(ok(TOOL_MSG))
    resp = c.complete([{"role": "user", "content": "hi"}], [{"type": "function"}])
    assert resp.tool_calls[0].name == "lookup_order"
    assert resp.tool_calls[0].arguments == {"order_id": "ORD-1001"}
    assert calls[0]["temperature"] == 0 and calls[0]["tools"]


def test_parses_plain_answer():
    c, _ = client_for(ok({"content": "done"}))
    assert c.complete([], []).content == "done"


def test_retries_rate_limit_then_succeeds():
    c, calls = client_for(httpx.Response(429), ok({"content": "done"}))
    assert c.complete([], []).content == "done" and len(calls) == 2


def test_retries_timeouts_then_gives_up():
    c, calls = client_for(*[httpx.ReadTimeout("slow")] * 4)
    with pytest.raises(LLMError, match="timed out"):
        c.complete([], [])
    assert len(calls) == 4  # 1 attempt + 3 retries


def test_auth_error_is_not_retried():
    c, calls = client_for(httpx.Response(401))
    with pytest.raises(LLMError, match="401"):
        c.complete([], [])
    assert len(calls) == 1


def test_malformed_body_raises():
    c, _ = client_for(httpx.Response(200, json={"error": "weird"}))
    with pytest.raises(LLMError, match="malformed"):
        c.complete([], [])


def test_unparseable_tool_arguments_are_passed_on_for_validation():
    bad = {"tool_calls": [{"id": "c1", "function": {"name": "lookup_order", "arguments": "{oops"}}]}
    c, _ = client_for(ok(bad))
    assert c.complete([], []).tool_calls[0].arguments == {"_unparseable_arguments": "{oops"}


def test_missing_key_is_rejected_at_startup():
    with pytest.raises(ValueError):
        ChatCompletionsClient("gemini", "", "m", timeout=1)


def test_unknown_provider_is_rejected():
    with pytest.raises(ValueError, match="unknown"):
        ChatCompletionsClient("acme", "key", "m", timeout=1)


def test_gemini_uses_google_openai_compatible_endpoint():
    c, _ = client_for(ok({"content": "hi"}), provider="gemini")
    c.complete([], [])
    assert c.urls[0].startswith("https://generativelanguage.googleapis.com/v1beta/openai/")
    assert c.name.startswith("gemini:")


def test_rate_limit_honours_retry_after(monkeypatch):
    slept = []
    monkeypatch.setattr("app.agent.llm.time.sleep", slept.append)
    c, _ = client_for(httpx.Response(429, headers={"Retry-After": "7"}), ok({"content": "ok"}))
    assert c.complete([], []).content == "ok"
    assert slept == [7.0]


def test_provider_extra_is_echoed_back_but_kept_out_of_traces():
    msg = {
        "tool_calls": [
            {
                "id": "c1",
                "extra_content": {"google": {"thought_signature": "sig"}},
                "function": {"name": "lookup_order", "arguments": "{}"},
            }
        ]
    }
    c, _ = client_for(ok(msg), provider="gemini")
    resp = c.complete([], [])
    assert resp.as_message()["tool_calls"][0]["extra_content"] == {
        "google": {"thought_signature": "sig"}
    }
    assert "provider_extra" not in resp.tool_calls[0].model_dump()


def test_rate_limit_honours_google_retry_info_body(monkeypatch):
    slept = []
    monkeypatch.setattr("app.agent.llm.time.sleep", slept.append)
    body = [
        {
            "error": {
                "code": 429,
                "details": [
                    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "12s"}
                ],
            }
        }
    ]
    c, _ = client_for(httpx.Response(429, json=body), ok({"content": "ok"}), provider="gemini")
    assert c.complete([], []).content == "ok"
    assert slept == [12.0]
