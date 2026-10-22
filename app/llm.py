"""Provider selection and a Responses API adapter for the guarded chat pipeline.

Only text/structured JSON calls are supported; the legacy Anthropic tool loop and
offline batch tagger are intentionally separate from the production chat path.
"""
import os
from contextlib import contextmanager
from types import SimpleNamespace

import anthropic
import httpx2
from openai import OpenAI


def provider_name():
    provider = os.getenv("LLM_PROVIDER", "anthropic").strip().lower()
    if provider not in {"anthropic", "openai"}:
        raise ValueError("LLM_PROVIDER must be anthropic or openai")
    return provider


def make_client():
    if provider_name() == "openai":
        return OpenAIChat(OpenAI(api_key=os.getenv("OPENAI_API_KEY") or "not-configured"))
    return anthropic.Anthropic(
        api_key=os.getenv("ANTHROPIC_API_KEY") or "not-configured",
        http_client=anthropic.DefaultHttpxClient(
            transport=httpx2.HTTPTransport(local_address="0.0.0.0")))


def normalized(response):
    refused = any(getattr(part, "type", "") == "refusal"
                  for item in response.output
                  for part in getattr(item, "content", []))
    usage = response.usage
    return SimpleNamespace(
        stop_reason="end_turn" if response.status == "completed" and not refused else "refusal",
        content=[SimpleNamespace(type="text", text=response.output_text)],
        usage=SimpleNamespace(input_tokens=getattr(usage, "input_tokens", 0),
                              output_tokens=getattr(usage, "output_tokens", 0),
                              cache_read_input_tokens=0, cache_creation_input_tokens=0,
                              provider="openai"))


class OpenAIChat:
    """Adapt only the message methods consumed by the router and answer pipeline."""
    def __init__(self, client):
        self.client = client
        self.messages = self

    def with_options(self, **kwargs):
        return OpenAIChat(self.client.with_options(**kwargs))

    def params(self, kwargs):
        router = kwargs["model"] == "claude-haiku-4-5"
        model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
        if router:
            model = os.getenv("OPENAI_ROUTER_MODEL") or model
        return dict(model=model, instructions=kwargs["system"],
                    input=kwargs["messages"], store=False,
                    max_output_tokens=max(256, kwargs["max_tokens"]),
                    text={"format": {"type": "json_schema", "name": "course_response",
                                     "strict": True,
                                     "schema": kwargs["output_config"]["format"]["schema"]}})

    def create(self, **kwargs):
        return normalized(self.client.responses.create(**self.params(kwargs)))

    @contextmanager
    def stream(self, **kwargs):
        with self.client.responses.create(**self.params(kwargs), stream=True) as events:
            yield OpenAIStream(events)


class OpenAIStream:
    def __init__(self, events):
        self.events = events
        self.final = None

    def __iter__(self):
        for event in self.events:
            if event.type == "response.output_text.delta":
                yield SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(
                    type="text_delta", text=event.delta))
            elif event.type in {"response.completed", "response.incomplete", "response.failed"}:
                self.final = normalized(event.response)
            elif event.type == "error":
                raise RuntimeError("OpenAI response stream failed")

    def get_final_message(self):
        if self.final is None:
            raise RuntimeError("OpenAI response stream ended without completion")
        return self.final
