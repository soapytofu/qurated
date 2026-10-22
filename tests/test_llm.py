"""Provider contract tests: no paid API calls or real credentials."""
from contextlib import contextmanager
from types import SimpleNamespace as NS

import pytest
import llm
import pipeline
from test_retrieval import database


def response(status="completed", refusal=False):
    return NS(status=status, output=[NS(content=[NS(type="refusal" if refusal else "output_text")])],
              output_text='{"needs_q_data": false}', usage=NS(input_tokens=10, output_tokens=5))


def test_selection_and_isolated_credentials(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    assert llm.provider_name() == "anthropic"
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    client = llm.make_client()
    assert isinstance(client, llm.OpenAIChat)
    assert client.client.api_key == "test-openai-key"
    client.client.close()
    monkeypatch.setenv("LLM_PROVIDER", "typo")
    with pytest.raises(ValueError):
        llm.make_client()


def test_router_request_and_options(monkeypatch):
    captured = {}
    def create(**kwargs):
        captured.update(kwargs)
        return response()
    client = llm.OpenAIChat(NS(responses=NS(create=create)))
    monkeypatch.setenv("OPENAI_MODEL", "answer-model")
    monkeypatch.setenv("OPENAI_ROUTER_MODEL", "router-model")
    result = client.create(model="claude-haiku-4-5", max_tokens=50,
                           system="trusted policy", messages=[{"role": "user", "content": "hello"}],
                           output_config={"format": {"schema": {"type": "object"}}})
    assert captured["model"] == "router-model"
    assert captured["instructions"] == "trusted policy"
    assert captured["store"] is False
    assert captured["text"]["format"]["strict"] is True
    assert "output_config" not in captured and "thinking" not in captured
    assert result.stop_reason == "end_turn"
    assert result.content[0].text == '{"needs_q_data": false}'


@pytest.mark.parametrize("status,refusal,reply,accepted", [
    ("completed", False, '{"picks":[],"advice":"Ask your adviser."}', True),
    ("incomplete", False, '{"picks":[],"advice":"Do not show."}', False),
    ("failed", False, '{"picks":[],"advice":"Do not show."}', False),
    ("completed", True, '{"picks":[],"advice":"Do not show."}', False),
    ("completed", False, '{"picks":[{"code":"FAKE 1","reason":"fake","quote":""}],"advice":"fake"}', False),
    ("completed", False, '{"picks":[],"advice":"123-45-6789"}', False),
])
def test_openai_stream_uses_existing_guardrails(status, refusal, reply, accepted):
    closed = []
    @contextmanager
    def create(**kwargs):
        assert kwargs["stream"] and "untrusted data" in kwargs["instructions"]
        assert kwargs["text"]["format"]["schema"] == pipeline.ANSWER_SCHEMA
        try:
            yield iter([NS(type="response.output_text.delta", delta=reply),
                        NS(type="response." + status, response=response(status, refusal))])
        finally:
            closed.append(True)
    sdk = NS(responses=NS(create=create))
    sdk.with_options = lambda **kwargs: sdk
    history = []
    events = list(pipeline.stream_answer(llm.OpenAIChat(sdk), database(), history,
                  "hello", {}, [], [], False, lambda x: x, lambda x: None, lambda *a: None))
    assert any(e["type"] == "text" for e in events) == accepted
    assert bool(history) == accepted
    assert closed


def test_interrupted_stream_rejected():
    stream = llm.OpenAIStream(iter([]))
    list(stream)
    with pytest.raises(RuntimeError):
        stream.get_final_message()


def test_missing_selected_key_never_calls_router(monkeypatch):
    import server
    monkeypatch.setattr(server, "PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(server, "needs_q_data", lambda *a: pytest.fail("network call"))
    events = list(server._run_fast_chat([], [], "hello", {}, [], [], {}))
    assert "OPENAI_API_KEY" in events[0]


def test_real_sdk_serializes_responses_request(monkeypatch):
    import json
    import httpx2
    import openai
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4.1-mini")
    def handle(request):
        assert request.url.path == "/v1/responses"
        payload = json.loads(request.content)
        assert payload["model"] == "gpt-4.1-mini"
        assert payload["store"] is False
        assert payload["text"]["format"]["strict"] is True
        reply = {"id": "resp_test", "object": "response", "created_at": 0,
                 "status": "completed", "model": "gpt-4.1-mini",
                 "output": [{"type": "message", "id": "msg_test", "role": "assistant",
                             "status": "completed", "content": [{"type": "output_text",
                             "text": '{"needs_q_data":false}', "annotations": []}]}],
                 "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}}
        return httpx2.Response(200, json=reply)
    with openai.OpenAI(api_key="test-only", http_client=openai.DefaultHttpxClient(
            transport=httpx2.MockTransport(handle))) as sdk:
        result = llm.OpenAIChat(sdk).create(model="claude-haiku-4-5", max_tokens=50,
                    system="classify", messages=[{"role": "user", "content": "hello"}],
                    output_config={"format": {"schema": {"type": "object"}}})
        assert result.content[0].text == '{"needs_q_data":false}'
        assert result.stop_reason == "end_turn"
