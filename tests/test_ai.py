from types import SimpleNamespace
from typing import Any

import pytest

from tradzlog_api.services import ai


class FakeMessages:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self.response


def fake_client(monkeypatch: pytest.MonkeyPatch, response: Any) -> SimpleNamespace:
    client = SimpleNamespace(messages=FakeMessages(response), beta=SimpleNamespace(messages=FakeMessages(response)))
    monkeypatch.setattr(ai, "Anthropic", lambda api_key: client)
    monkeypatch.setattr(ai.settings, "anthropic_api_key", "test-key")
    return client


def reply(stop_reason: str = "end_turn", text: str = '{"insights": []}', stop_details: Any = None) -> SimpleNamespace:
    return SimpleNamespace(
        stop_reason=stop_reason,
        stop_details=stop_details,
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
    )


def test_current_sonnet_uses_server_side_fallbacks_and_room_for_thinking(monkeypatch) -> None:
    monkeypatch.setattr(ai.settings, "anthropic_model", "claude-sonnet-5-5")
    client = fake_client(monkeypatch, reply())
    insight = ai.generate_coaching_insight("u1", "Tarik", None, "{}")

    (call,) = client.beta.messages.calls
    assert client.messages.calls == []
    assert call["model"] == "claude-sonnet-5-5"
    assert call["fallbacks"] == "default" and call["betas"] == ["server-side-fallback-2026-07-01"]
    assert call["max_tokens"] >= 16000
    assert "temperature" not in call and "thinking" not in call
    assert insight.content == '{"insights": []}'  # thinking blocks are skipped


def test_other_models_use_the_plain_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(ai.settings, "anthropic_model", "claude-haiku-5-5")
    client = fake_client(monkeypatch, reply())
    ai.generate_coaching_insight("u1", "Tarik", None, "{}")
    (call,) = client.messages.calls
    assert "fallbacks" not in call and client.beta.messages.calls == []


def test_refusal_and_truncation_are_reported_not_stored_as_analysis(monkeypatch) -> None:
    monkeypatch.setattr(ai.settings, "anthropic_model", "claude-sonnet-5-5")
    fake_client(monkeypatch, reply("refusal", "", SimpleNamespace(category="general_harms")))
    refused = ai.generate_coaching_insight("u1", "Tarik", None, "{}")
    assert "declined: general_harms" in refused.content

    fake_client(monkeypatch, reply("max_tokens", "partial"))
    assert ai.generate_coaching_insight("u1", "Tarik", None, "{}").content.endswith("(Analysis was cut short.)")
